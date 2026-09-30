from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zf.core.config.schema import (
    ExternalIssueIngressConfig,
    SelfIssueConfig,
    SelfIssueTargetConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.forge.github_issues import (
    GitHubIssueReconciler,
    normalize_github_comment,
    normalize_github_issue,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.external_issue_label_sync import _relevant_events
from zf.web.issue_triage_routes import build_issue_triage_router
from zf.web.issue_triage_routes import _workflow_projection
from zf.web.server import (
    _web_action,
    _web_action_requires_issue_triage_capability,
    _web_triage_auth_error,
    create_app,
)


@pytest.fixture(autouse=True)
def _isolate_remote_lifecycle_writes(monkeypatch):
    factory = lambda: lambda *args: {"status": "disabled", "reason": "test_offline"}
    monkeypatch.setattr("zf.runtime.external_issue_ingress.build_github_lifecycle_label_writer", factory)
    monkeypatch.setattr("zf.web.issue_triage_routes.build_github_lifecycle_label_writer", factory)


def issue_payload(
    number: int = 7,
    *,
    updated_at: str = "2026-08-25T01:00:00Z",
    labels: tuple[str, ...] = ("performance", "p2"),
    body: str = "Observed slowdown",
) -> dict:
    return {
        "number": number,
        "node_id": f"I_{number}",
        "html_url": f"https://github.com/rzwu-uisee/zaofu-self-issue/issues/{number}",
        "title": f"Issue {number}",
        "body": body,
        "user": {
            "login": "reporter",
            "avatar_url": "https://avatars.githubusercontent.com/u/123?v=4",
        },
        "state": "open",
        "created_at": "2026-08-25T00:00:00Z",
        "updated_at": updated_at,
        "closed_at": None,
        "labels": [{"name": item} for item in labels],
        "assignees": [{
            "login": "maintainer",
            "avatar_url": "https://avatars.githubusercontent.com/u/321?v=4",
        }],
        "comments": 2,
        "milestone": {"title": "P0"},
    }


def comment_payload(comment_id: int = 91) -> dict:
    return {
        "id": comment_id,
        "node_id": f"IC_{comment_id}",
        "html_url": f"https://github.com/rzwu-uisee/zaofu-self-issue/issues/7#issuecomment-{comment_id}",
        "body": "Screenshot: ![capture](https://github.com/user-attachments/assets/example.png)",
        "user": {
            "login": "commenter",
            "avatar_url": "https://avatars.githubusercontent.com/u/789?v=4",
        },
        "created_at": "2026-08-25T02:10:00Z",
        "updated_at": "2026-08-25T02:10:00Z",
        "author_association": "CONTRIBUTOR",
    }


def test_relevant_events_decodes_rotated_event_logs_without_filename_prefix(tmp_path: Path) -> None:
    """Multi-file rg scans must keep JSONL decodable for live projections."""
    active = tmp_path / "events.jsonl"
    first_log = EventLog(active)
    first = EventWriter(first_log).emit(
        "external_issue.received",
        actor="test",
        payload={"source_key": "github:repo:65"},
    )
    archive_dir = tmp_path / "events"
    archive_dir.mkdir()
    active.rename(archive_dir / "2026-09-28.jsonl")
    second = EventWriter(EventLog(active)).emit(
        "external_issue.triage.queued",
        actor="test",
        task_id="ISSUE-65",
        payload={"source_key": "github:repo:65", "workflow_run_id": "TRIAGE-65"},
    )
    selected = _relevant_events(EventLog(active), {"github:repo:65"})
    assert {event.id for event in selected} == {first.id, second.id}


def test_normalization_derives_group_and_rejects_pull_requests() -> None:
    item, body = normalize_github_issue(
        issue_payload(body="<!-- zf-self-issue:stable -->\nObserved slowdown"),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    assert item.derived_group == "triaged"
    assert item.source == "self_issue"
    assert item.author_avatar_url == "https://avatars.githubusercontent.com/u/123?v=4"
    assert item.assignee_avatar_urls == {
        "maintainer": "https://avatars.githubusercontent.com/u/321?v=4",
    }
    assert body.startswith("<!-- zf-self-issue:")

    colored, _ = normalize_github_issue(
        issue_payload(labels=("performance",)),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    assert colored.label_colors == {}

    payload = issue_payload(labels=("performance",))
    payload["labels"] = [{"name": "performance", "color": "D93F0B"}]
    colored, _ = normalize_github_issue(
        payload,
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    assert colored.label_colors == {"performance": "D93F0B"}

    pull_request = issue_payload()
    pull_request["pull_request"] = {"url": "https://api.github.com/pulls/7"}
    with pytest.raises(ValueError, match="pull_request_not_issue"):
        normalize_github_issue(
            pull_request,
            repository="rzwu-uisee/zaofu-self-issue",
            repository_id="123",
            seen_at="2026-08-25T02:00:00+00:00",
        )

    comment = normalize_github_comment(comment_payload())
    assert comment.author_login == "commenter"
    assert "user-attachments" in comment.body


def test_remote_lifecycle_label_is_projection_authority_without_local_events() -> None:
    mirror, _ = normalize_github_issue(
        issue_payload(labels=("bug", "merged")),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )

    projection = _workflow_projection([], mirror)

    assert projection is not None
    assert projection["state"] == "merged"
    assert projection["remote_lifecycle_state"] == "merged"


def test_store_keeps_body_sidecar_and_rejects_older_revision(tmp_path: Path) -> None:
    store = IssueMirrorStore(tmp_path / "state")
    newer, body = normalize_github_issue(
        issue_payload(updated_at="2026-08-25T02:00:00Z"),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:01:00+00:00",
    )
    saved, changed = store.upsert(newer, body)
    assert changed
    assert "body" not in json.loads(store.issues_path.read_text(encoding="utf-8"))[0]
    assert store.read_body(saved) == body

    older, older_body = normalize_github_issue(
        issue_payload(updated_at="2026-08-25T01:00:00Z", body="stale"),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:02:00+00:00",
    )
    retained, changed = store.upsert(older, older_body)
    assert not changed
    assert retained.updated_at == "2026-08-25T02:00:00Z"
    assert store.read_body(retained) == body
    comment = normalize_github_comment(comment_payload())
    with_comments, changed = store.write_comments(retained, [comment])
    assert changed
    assert store.read_comments(with_comments) == [comment]


def test_reconciler_verifies_repository_filters_pr_and_debounces(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(method: str, url: str, headers: dict[str, str]):
        del method, headers
        calls.append(url)
        if url.endswith("/repos/rzwu-uisee/zaofu-self-issue"):
            return 200, json.dumps({
                "id": 123,
                "full_name": "rzwu-uisee/zaofu-self-issue",
                "stargazers_count": 64500,
            }).encode(), {}
        if "/comments?" in url:
            return 200, json.dumps([comment_payload()]).encode(), {}
        pull_request = issue_payload(8)
        pull_request["pull_request"] = {"url": "https://api.github.com/pulls/8"}
        return 200, json.dumps([issue_payload(), pull_request]).encode(), {
            "ETag": '"one"',
            "X-RateLimit-Remaining": "59",
        }

    reconciler = GitHubIssueReconciler(
        tmp_path / "state",
        "rzwu-uisee/zaofu-self-issue",
        transport=transport,
        now=lambda: datetime(2026, 8, 25, 2, tzinfo=timezone.utc),
    )
    assert reconciler.refresh() == {"ok": True, "status": "fresh", "changed": 2}
    assert [item.number for item in reconciler.store.list()] == [7]
    assert reconciler.store.sync_state().rate_limit_remaining == 59
    assert reconciler.store.sync_state().star_count == 64500
    mirrored = reconciler.store.get(7)
    assert mirrored is not None
    assert reconciler.store.read_comments(mirrored)[0].author_login == "commenter"
    assert reconciler.refresh().get("debounced") is True
    assert len(calls) == 3


def test_reconciler_can_fetch_one_unmirrored_issue(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(method: str, url: str, headers: dict[str, str]):
        del method, headers
        calls.append(url)
        if url.endswith("/repos/rzwu-uisee/zaofu-self-issue"):
            return 200, json.dumps({
                "id": 123,
                "full_name": "rzwu-uisee/zaofu-self-issue",
                "stargazers_count": 7,
            }).encode(), {}
        if "/comments?" in url:
            return 200, json.dumps([comment_payload()]).encode(), {}
        if url.endswith("/issues/7"):
            return 200, json.dumps(issue_payload()).encode(), {
                "X-RateLimit-Remaining": "58",
            }
        raise AssertionError(url)

    reconciler = GitHubIssueReconciler(
        tmp_path / "state",
        "rzwu-uisee/zaofu-self-issue",
        transport=transport,
        now=lambda: datetime(2026, 8, 25, 2, tzinfo=timezone.utc),
    )

    result = reconciler.refresh_issue(7)

    assert result == {"ok": True, "status": "fresh", "changed": 2, "issue_number": 7}
    assert reconciler.store.get(7) is not None
    assert reconciler.store.sync_state().rate_limit_remaining == 58
    assert not any("/issues?" in url for url in calls)


@dataclass
class FakeContext:
    state_dir: Path
    config: ZfConfig


def configured_context(state_dir: Path) -> FakeContext:
    config = ZfConfig()
    config.self_issue = SelfIssueConfig(
        enabled=True,
        target_locked=True,
        targets={
            "github": SelfIssueTargetConfig(
                provider="github",
                authorization_domain="github.com",
                project="rzwu-uisee/zaofu-self-issue",
            ),
        },
    )
    return FakeContext(state_dir=state_dir, config=config)


def test_routes_list_detail_and_signed_webhook(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        webhook_secret=lambda: "test-secret",
    ))
    client = TestClient(app)
    webhook_issue = issue_payload()
    webhook_issue["labels"][0]["color"] = "D93F0B"
    raw = json.dumps({
        "action": "opened",
        "repository": {"id": 123, "full_name": "rzwu-uisee/zaofu-self-issue"},
        "issue": webhook_issue,
    }).encode()
    signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }
    response = client.post(
        "/api/projects/test/issue-triage/github-webhook",
        content=raw,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert client.post(
        "/api/projects/test/issue-triage/github-webhook",
        content=raw,
        headers=headers,
    ).json()["status"] == "duplicate"

    page = client.get("/api/projects/test/issue-triage?group=triaged").json()
    assert page["total"] == 1
    assert page["items"][0]["number"] == 7
    assert page["items"][0]["author_avatar_url"].startswith("https://avatars.githubusercontent.com/")
    assert client.get("/api/projects/test/issue-triage/summary").json()["label_colors"] == {
        "performance": "D93F0B",
    }
    assert client.get("/api/projects/test/issue-triage/summary").json()["author_states"] == {
        "reporter": {"open": 1, "closed": 0},
    }
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"labels": json.dumps(["performance"])},
    ).json()["total"] == 1
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"labels": json.dumps([])},
    ).json()["total"] == 0

    second_payload = issue_payload(8, labels=("p1",))
    second_payload["title"] = "Alpha issue"
    second_payload["created_at"] = "2026-08-24T00:00:00Z"
    second_payload["updated_at"] = "2026-08-26T00:00:00Z"
    second_payload["user"] = {
        "login": "another-reporter",
        "avatar_url": "https://avatars.githubusercontent.com/u/456?v=4",
    }
    second_payload["labels"] = [{"name": "p1", "color": "B60205"}]
    second_payload["body"] = "Issue from /issue <!-- zf-self-issue:fixture -->"
    second, second_body = normalize_github_issue(
        second_payload,
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-26T00:01:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(second, second_body)
    stored_first = IssueMirrorStore(ctx.state_dir).get(7)
    assert stored_first is not None
    IssueMirrorStore(ctx.state_dir).write_comments(
        stored_first,
        [normalize_github_comment(comment_payload())],
    )
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"authors": json.dumps(["another-reporter"])},
    ).json()["items"][0]["number"] == 8
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"authors": json.dumps([])},
    ).json()["total"] == 0
    by_name = client.get(
        "/api/projects/test/issue-triage",
        params={"order_by": "name", "order_direction": "asc"},
    ).json()
    assert [item["number"] for item in by_name["items"]] == [8, 7]
    by_created = client.get(
        "/api/projects/test/issue-triage",
        params={"order_by": "created", "order_direction": "asc"},
    ).json()
    assert [item["number"] for item in by_created["items"]] == [8, 7]
    by_updated = client.get(
        "/api/projects/test/issue-triage",
        params={"order_by": "updated", "order_direction": "asc"},
    ).json()
    assert [item["number"] for item in by_updated["items"]] == [7, 8]
    by_created_name = client.get(
        "/api/projects/test/issue-triage",
        params={"order_by": "created_name", "order_direction": "asc"},
    ).json()
    assert [item["number"] for item in by_created_name["items"]] == [8, 7]
    by_sources = client.get(
        "/api/projects/test/issue-triage",
        params={"sources": json.dumps(["self_issue"])},
    ).json()
    assert [item["number"] for item in by_sources["items"]] == [8]
    by_number = client.get(
        "/api/projects/test/issue-triage",
        params={"order_by": "number", "order_direction": "asc"},
    ).json()
    assert [item["number"] for item in by_number["items"]] == [7, 8]
    detail = client.get("/api/projects/test/issue-triage/7").json()
    assert detail["body"] == "Observed slowdown"
    assert detail["comments"][0]["author_login"] == "commenter"
    assert "user-attachments" in detail["comments"][0]["body"]
    assert detail["trust"] == "untrusted_external_input"

    invalid = client.post(
        "/api/projects/test/issue-triage/github-webhook",
        content=raw,
        headers={**headers, "X-Hub-Signature-256": "sha256=bad"},
    )
    assert invalid.status_code == 403


def test_create_app_registers_issue_triage_routes(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    app = create_app(ctx.state_dir, config=ctx.config, project_root=tmp_path)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/projects/{project_id}/issue-triage" in paths
    assert "/api/projects/{project_id}/issue-triage/refresh" in paths
    assert "/api/projects/{project_id}/issue-triage/start-triage" in paths
    assert "/api/projects/{project_id}/issue-triage/{issue_number}/candidate-review" in paths
    assert "/api/projects/{project_id}/issue-triage/{issue_number}/prepare-publication" in paths
    assert "/api/projects/{project_id}/issue-triage/{issue_number}/pull-request" in paths
    assert "/api/projects/{project_id}/issue-triage/{issue_number}/pull-request/refresh" in paths
    assert "/api/projects/{project_id}/issue-triage/attachment" in paths
    assert "/api/projects/{project_id}/issue-triage/github-webhook" in paths


def test_production_triage_requires_distinct_developer_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = configured_context(tmp_path / "state")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "reporter-token")
    monkeypatch.setenv("ZF_WEB_DEVELOPER_TOKEN", "maintainer-token")
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda _project_id: ctx,
        mutation_auth_error=_web_triage_auth_error,
        triage_view_auth_error=_web_triage_auth_error,
    ))
    client = TestClient(app)

    reporter = client.get(
        "/api/projects/test/issue-triage/summary",
        headers={"X-ZF-Web-Token": "reporter-token"},
    )
    maintainer = client.get(
        "/api/projects/test/issue-triage/summary",
        headers={
            "X-ZF-Web-Token": "maintainer-token",
            "X-ZF-Developer-Token": "maintainer-token",
        },
    )
    assert reporter.status_code == 403
    assert reporter.json()["status"] == "developer_capability_required"
    refresh_reporter = client.post(
        "/api/projects/test/issue-triage/refresh",
        headers={"X-ZF-Web-Token": "reporter-token"},
    )
    assert refresh_reporter.status_code == 403
    assert refresh_reporter.json()["status"] == "developer_capability_required"
    assert maintainer.status_code == 200

    server = create_app(ctx.state_dir, config=ctx.config, project_root=tmp_path)
    session_client = TestClient(server)
    session_reporter = session_client.get(
        "/api/web-session",
        headers={"X-ZF-Web-Token": "reporter-token"},
    )
    session_maintainer = session_client.get(
        "/api/web-session",
        headers={
            "X-ZF-Web-Token": "maintainer-token",
            "X-ZF-Developer-Token": "maintainer-token",
        },
    )
    assert session_reporter.json()["can_view_issue_triage"] is False
    assert session_maintainer.json()["can_view_issue_triage"] is True


def test_generic_issue_actions_keep_reporters_out_of_developer_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = configured_context(tmp_path / "state")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "reporter-token")
    monkeypatch.setenv("ZF_WEB_DEVELOPER_TOKEN", "maintainer-token")

    reporter = _web_action(
        ctx.state_dir,
        "external-issue-triage-queued-cancel",
        payload={"triage_id": "TRIAGE-7"},
        authorization="Bearer reporter-token",
        x_zf_web_token="reporter-token",
        config=ctx.config,
        project_root=tmp_path,
    )
    assert reporter["_status_code"] == 403
    assert reporter["status"] == "developer_capability_required"

    maintainer = _web_action(
        ctx.state_dir,
        "external-issue-triage-queued-cancel",
        payload={"triage_id": "TRIAGE-7"},
        authorization="Bearer reporter-token",
        x_zf_web_token="reporter-token",
        x_zf_developer_token="maintainer-token",
        config=ctx.config,
        project_root=tmp_path,
    )
    assert maintainer["status"] != "unauthorized"

    client = TestClient(create_app(ctx.state_dir, config=ctx.config, project_root=tmp_path))
    reporter_response = client.post(
        "/api/actions/external-issue-triage-queued-cancel",
        json={"triage_id": "TRIAGE-7"},
        headers={"X-ZF-Web-Token": "reporter-token"},
    )
    assert reporter_response.status_code == 403
    assert reporter_response.json()["status"] == "developer_capability_required"
    maintainer_response = client.post(
        "/api/actions/external-issue-triage-queued-cancel",
        json={"triage_id": "TRIAGE-7"},
        headers={
            "X-ZF-Web-Token": "reporter-token",
            "X-ZF-Developer-Token": "maintainer-token",
        },
    )
    assert maintainer_response.json()["status"] != "developer_capability_required"
    assert maintainer.get("status") != "developer_capability_required"


def test_issue_run_and_workflow_actions_are_detected_from_ledger(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "workflow.invoke.requested",
        actor="web",
        task_id="ISSUE-7",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-7"},
    )
    assert _web_action_requires_issue_triage_capability(
        state_dir, "run-cancel", {"run_id": "FIX-7"},
    ) is True
    assert _web_action_requires_issue_triage_capability(
        state_dir, "run-cancel", {"run_id": "ordinary-run"},
    ) is False
    assert _web_action_requires_issue_triage_capability(
        state_dir, "workflow-invoke", {"flow_kind": "issue"},
    ) is True
    assert _web_action_requires_issue_triage_capability(
        state_dir, "workflow-invoke", {"flow_kind": "workflow"},
    ) is False


def test_attachment_proxy_only_serves_github_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = configured_context(tmp_path / "state")

    class ImageResponse:
        headers = {"Content-Type": "image/png"}

        def __enter__(self) -> ImageResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 20_000_001
            return b"\x89PNG\r\n"

    def fake_urlopen(request: object, timeout: int) -> ImageResponse:
        assert getattr(request, "full_url", "").startswith(
            "https://github.com/user-attachments/assets/",
        )
        assert timeout == 20
        return ImageResponse()

    monkeypatch.setattr("zf.web.issue_triage_routes.urllib.request.urlopen", fake_urlopen)
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)
    response = client.get(
        "/api/projects/test/issue-triage/attachment",
        params={"url": "https://github.com/user-attachments/assets/example.png"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n"
    assert client.get(
        "/api/projects/test/issue-triage/attachment",
        params={"url": "https://example.com/private.png"},
    ).status_code == 400


def test_manual_refresh_forces_repository_metadata_sync(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    force_values: list[bool] = []

    class Reconciler:
        def refresh(self, *, force: bool = False) -> dict:
            force_values.append(force)
            return {"ok": True, "status": "fresh", "changed": 0}

    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        reconciler_factory=lambda state_dir, repository: Reconciler(),  # type: ignore[arg-type]
        mutation_auth_error=lambda *args, **kwargs: None,
    ))
    client = TestClient(app)
    assert client.post(
        "/api/projects/test/issue-triage/refresh?force=true",
    ).status_code == 200
    assert force_values == [True]


@pytest.mark.parametrize("ingress_enabled", [True, False])
def test_manual_start_triage_admits_one_historical_issue(
    tmp_path: Path, ingress_enabled: bool,
) -> None:
    ctx = configured_context(tmp_path / "state")
    ctx.state_dir.mkdir(parents=True)
    (ctx.state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    ctx.config.self_issue.ingress = ExternalIssueIngressConfig(enabled=ingress_enabled)
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    requested: list[int] = []

    class Reconciler:
        def refresh_issue(self, issue_number: int) -> dict:
            requested.append(issue_number)
            return {"ok": True, "status": "fresh", "changed": 0, "issue_number": issue_number}

    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        reconciler_factory=lambda state_dir, repository: Reconciler(),  # type: ignore[arg-type]
        mutation_auth_error=lambda *args, **kwargs: None,
    ))
    client = TestClient(app)

    wrong_repo = client.post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": "https://github.com/another/project/issues/7"},
    )
    response = client.post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": "#7"},
    )
    duplicate = client.post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": mirror.html_url},
    )

    assert wrong_repo.status_code == 422
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["issue"]["workflow"]["state"] == "triage_queued"
    assert duplicate.json()["status"] == "already_queued"
    assert requested == [7, 7]


def test_cancelled_triage_can_restart_with_automatic_ingress_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = configured_context(tmp_path / "state")
    ctx.config.self_issue.ingress.enabled = True
    mirror, body = normalize_github_issue(
        issue_payload(), repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123", seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    requested: list[int] = []

    class Reconciler:
        def refresh_issue(self, issue_number: int) -> dict:
            requested.append(issue_number)
            return {"ok": True, "status": "fresh", "issue_number": issue_number}

        def refresh(self, **kwargs) -> dict:
            pytest.fail("Manual Triage must not start repository-wide synchronization")

    monkeypatch.setattr(
        "zf.web.issue_triage_routes.build_github_lifecycle_label_writer",
        lambda: lambda *args: {"status": "reconciled"},
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda _: ctx,
        reconciler_factory=lambda *args: Reconciler(),
        mutation_auth_error=lambda *args, **kwargs: None,
    ))
    client = TestClient(app)
    base = "/api/projects/test/issue-triage"
    first = client.post(base + "/start-triage", json={"issue": "#7"})
    assert first.status_code == 200, first.text
    first_run = first.json()["workflow_run_id"]
    cancelled = client.post(base + "/7/cancel-queued", json={"triage_id": first_run})
    assert cancelled.status_code == 200, cancelled.text
    assert client.get(base + "/7").json()["issue"]["workflow"]["state"] == "triage_cancelled"

    ctx.config.self_issue.ingress.enabled = False
    restarted = client.post(base + "/start-triage", json={"issue": "#7"})
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["status"] == "queued"
    assert restarted.json()["task_id"] == first.json()["task_id"]
    assert restarted.json()["workflow_run_id"] != first_run
    duplicate = client.post(base + "/start-triage", json={"issue": "#7"})
    assert duplicate.json()["status"] == "already_queued"
    assert duplicate.json()["workflow_run_id"] == restarted.json()["workflow_run_id"]
    assert client.get(base + "/7").json()["issue"]["workflow"]["state"] == "triage_queued"
    assert client.get(base).json()["items"][0]["workflow"]["state"] == "triage_queued"
    assert client.get(base + "/summary").json()["states"]["triage_queued"] == 1
    assert ctx.config.self_issue.ingress.enabled is False
    assert requested == [7, 7, 7]
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    queued = [event for event in events if event.type == "external_issue.triage.queued"]
    assert len(queued) == 2
    assert queued[-1].payload["retry_of_workflow_run_id"] == first_run


@pytest.mark.parametrize("lane", ["triage", "fix"])
@pytest.mark.parametrize("failure", [False, True])
def test_cancel_reconciles_remote_and_persisted_labels(tmp_path: Path, monkeypatch, lane, failure):
    from zf.integrations.forge.github import GitHubComProvider
    from zf.runtime.external_issue_ingress import ExternalIssueIngressService

    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(labels=("bug", "p1", f"{lane}_queued")),
        repository="rzwu-uisee/zaofu-self-issue", repository_id="123", seen_at="2026-09-28T00:00:00Z",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    remote = list(mirror.labels)
    calls = []
    unavailable = failure

    def transport(method, url, headers, payload):
        calls.append(method)
        if unavailable:
            return 503, b"{}"
        if method == "GET":
            return 200, json.dumps([{"name": label, "color": "DC2626"} for label in remote]).encode()
        if method == "POST":
            if "/issues/" in url:
                remote.extend(json.loads(payload)["labels"])
                return 200, b"[]"
            return 201, b"{}"
        assert method == "DELETE"
        remote.remove(url.rsplit("/", 1)[1])
        return 204, b""

    forge = GitHubComProvider(transport)

    def label_writer(item, state):
        result = forge.reconcile_lifecycle_label(item.repository, item.number, state, access_token="test")
        return {"status": result.status, "reason": result.reason, "label_colors": dict(result.label_colors)}

    monkeypatch.setattr("zf.runtime.external_issue_ingress.build_github_lifecycle_label_writer", lambda: label_writer)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit("external_issue.triage.queued" if lane == "triage" else "workflow.invoke.requested",
                actor="kernel", task_id="TASK-7", payload={"source_key": mirror.issue_key,
                "workflow_run_id": "RUN-7", "flow_kind": "issue", "source_revision": "r1"})
    service = ControlledActionService(ctx.state_dir, writer, config=ctx.config, project_root=tmp_path)

    def cancel():
        requested = writer.emit("external_issue.triage.queued_cancel_requested", actor="web", payload={})
        return service.execute(action=f"external-issue-{lane}-queued-cancel", requested_action="cancel-queued",
                               payload={"triage_id": "RUN-7"}, requested=requested)

    result = cancel()
    assert result["status"] == "cancelled"
    if failure:
        assert result["lifecycle_label_sync"]["status"] != "reconciled"
        assert IssueMirrorStore(ctx.state_dir).get(7).labels == mirror.labels
        assert any(e.type == "external_issue.lifecycle_label.reconcile_failed" for e in writer.event_log.read_all())
        unavailable = False
        ingress = ExternalIssueIngressService(ctx.state_dir, ctx.config, project_root=tmp_path, lifecycle_label_writer=label_writer)
        ingress.reconcile_queue_cancellations()
    assert remote == ["bug", "p1", f"{lane}_cancelled"]
    assert IssueMirrorStore(ctx.state_dir).get(7).labels == tuple(remote)
    store = IssueMirrorStore(ctx.state_dir)
    assert store.read_body(store.get(7)) == body
    before = len(calls)
    assert cancel()["idempotent_replay"] is True
    assert len(calls) == before
    events = writer.event_log.read_all()
    assert sum(e.type == f"external_issue.{lane}.queued_cancelled" for e in events) == 1
    cancelled = next(e for e in events if e.type == f"external_issue.{lane}.queued_cancelled")
    writer.emit("workflow.invoke.requested", actor="kernel", payload={"source_key": mirror.issue_key, "workflow_run_id": "NEW-RUN"})
    ingress = ExternalIssueIngressService(ctx.state_dir, ctx.config, project_root=tmp_path, lifecycle_label_writer=label_writer)
    assert ingress.reconcile_queue_cancellation(cancelled)["status"] == "superseded"
    assert len(calls) == before


def test_manual_start_triage_inherits_locked_workspace_ingress(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    ctx.state_dir.mkdir(parents=True)
    (ctx.state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    workspace_config = configured_context(tmp_path / "workspace-state").config
    workspace_config.self_issue.ingress = ExternalIssueIngressConfig(enabled=True)
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)

    class Reconciler:
        def refresh_issue(self, issue_number: int) -> dict:
            return {
                "ok": True,
                "status": "fresh",
                "changed": 0,
                "issue_number": issue_number,
            }

    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        workspace_config=workspace_config,
        reconciler_factory=lambda state_dir, repository: Reconciler(),  # type: ignore[arg-type]
        mutation_auth_error=lambda *args, **kwargs: None,
    ))

    response = TestClient(app).post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": "#7"},
    )

    assert ctx.config.self_issue.ingress.enabled is False
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_manual_start_triage_requires_mutation_authorization(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))

    response = TestClient(app).post(
        "/api/projects/test/issue-triage/start-triage",
        json={"issue": "#7"},
    )

    assert response.status_code == 503
    assert response.json()["status"] == "disabled"


def test_candidate_delivery_routes_use_token_gate_and_deterministic_service(
    tmp_path: Path,
) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    calls: list[tuple[str, object]] = []

    class DeliveryService:
        def projection(self, issue):
            return {"enabled": True, "status": "verified_candidate"}

        def review(self, issue, **payload):
            calls.append(("review", payload))
            return {"ok": True, "status": "approved_for_pr"}

        def prepare(self, issue, **payload):
            calls.append(("prepare", payload))
            return {"ok": True, "status": "publication_prepared"}

        def record_pr(self, issue, *, url):
            calls.append(("record", url))
            return {"ok": True, "status": "pr_open"}

        def refresh_pr(self, issue):
            calls.append(("refresh", issue.number))
            return {"ok": True, "status": "merged"}

    actions: list[str] = []

    def authorize(action: str, **kwargs):
        actions.append(action)
        return None

    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        mutation_auth_error=authorize,
        delivery_service_factory=lambda context, config: DeliveryService(),  # type: ignore[arg-type]
    ))
    client = TestClient(app)

    detail = client.get("/api/projects/test/issue-triage/7")
    review = client.post(
        "/api/projects/test/issue-triage/7/candidate-review",
        json={
            "verdict": "approve", "candidate_sha": "a" * 40,
            "source_revision": "sha256:source",
        },
    )
    prepare = client.post(
        "/api/projects/test/issue-triage/7/prepare-publication",
        json={"candidate_sha": "a" * 40, "source_revision": "sha256:source"},
    )
    record = client.post(
        "/api/projects/test/issue-triage/7/pull-request",
        json={"url": "https://github.com/rzwu-uisee/zaofu-self-issue/pull/23"},
    )
    refresh = client.post(
        "/api/projects/test/issue-triage/7/pull-request/refresh",
        json={},
    )

    assert detail.json()["delivery"]["enabled"] is True
    assert [response.status_code for response in (review, prepare, record, refresh)] == [200] * 4
    assert actions == [
        "issue-candidate-review",
        "issue-candidate-prepare-publication",
        "issue-candidate-record-pr",
        "issue-candidate-refresh-pr",
    ]
    assert [name for name, _ in calls] == ["review", "prepare", "record", "refresh"]


def test_triage_read_projection_requires_developer_capability(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")

    def deny_view(action: str, **_kwargs):
        assert action == "issue-triage-view"
        return {
            "_status_code": 403,
            "ok": False,
            "status": "unauthorized",
            "reason": "developer capability required",
        }

    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda project_id: ctx,
        triage_view_auth_error=deny_view,
    ))
    client = TestClient(app)

    for path in (
        "/api/projects/test/issue-triage/summary",
        "/api/projects/test/issue-triage",
        "/api/projects/test/issue-triage/1",
    ):
        response = client.get(path)
        assert response.status_code == 403
        assert response.json()["status"] == "unauthorized"


def test_list_and_detail_project_external_issue_workflow_state(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    received = writer.emit(
        "external_issue.received",
        actor="github-poller",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
        },
    )
    writer.emit(
        "external_issue.triage.queued",
        actor="external-issue-intake",
        task_id="ISSUE-123",
        causation_id=received.id,
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
            "invoke_event_id": "evt-invoke",
            "status": "triage_queued",
        },
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)

    item = client.get("/api/projects/test/issue-triage").json()["items"][0]
    detail = client.get("/api/projects/test/issue-triage/7").json()["issue"]

    assert item["workflow"] == detail["workflow"]
    assert item["workflow"]["state"] == "triage_queued"
    assert detail["workflow"]["lifecycle"]["stages"]["triage"]["status"] == "recorded"
    assert detail["workflow"]["lifecycle"]["stages"]["fix"]["status"] == "unknown"
    assert item["workflow"]["task_id"] == "ISSUE-123"
    assert item["workflow"]["source_revision"] == "sha256:revision"
    assert item["workflow"]["triage_id"] == "TRIAGE-123"
    assert item["workflow"]["triage_history"][0]["triage_id"] == "TRIAGE-123"

    for query in ("ISSUE-123", "TRIAGE-123"):
        result = client.get(
            "/api/projects/test/issue-triage",
            params={"q": query},
        ).json()
        assert [entry["number"] for entry in result["items"]] == [7]

    writer.emit(
        "run.cancelled",
        actor="web",
        task_id="ISSUE-123",
        payload={
            "run_id": "TRIAGE-123",
            "workflow_run_id": "TRIAGE-123",
            "reason": "operator cancelled queued triage",
        },
    )
    cancelled = client.get("/api/projects/test/issue-triage/7").json()["issue"]
    assert cancelled["workflow"]["state"] == "triage_cancelled"


def test_workflow_projection_exposes_unverified_candidate(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(12), repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123", seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit("external_issue.triage.queued", actor="kernel", task_id="ISSUE-12",
                payload={"source_key": mirror.issue_key, "source_revision": "sha256:r",
                         "workflow_run_id": "TRIAGE-12", "status": "triage_queued"})
    writer.emit("external_issue.triage.unverified_candidate", actor="kernel", task_id="ISSUE-12",
                payload={"source_key": mirror.issue_key, "workflow_run_id": "TRIAGE-12",
                         "verification_status": "partial", "verification_gaps": ["runtime stopped"]})
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    projection = TestClient(app).get("/api/projects/test/issue-triage/12").json()["issue"]["workflow"]
    assert projection["state"] == "unverified_candidate"
    assert projection["verification_gaps"] == ["runtime stopped"]


def test_workflow_projection_exposes_unique_task_history_newest_first(
    tmp_path: Path,
) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(12), repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123", seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    for revision, task_id, run_id in (
        ("sha256:r1", "ISSUE-12", "TRIAGE-12"),
        ("sha256:r2", "ISSUE-12-R1", "TRIAGE-12-R1"),
        ("sha256:r2", "ISSUE-12-R1", "TRIAGE-12-R1-RETRY"),
    ):
        writer.emit(
            "external_issue.received", actor="github-poller",
            payload={"source_key": mirror.issue_key, "source_revision": revision},
        )
        writer.emit(
            "external_issue.triage.queued", actor="kernel", task_id=task_id,
            payload={
                "source_key": mirror.issue_key,
                "source_revision": revision,
                "workflow_run_id": run_id,
            },
        )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))

    workflow = TestClient(app).get(
        "/api/projects/test/issue-triage/12",
    ).json()["issue"]["workflow"]

    assert workflow["task_id"] == "ISSUE-12-R1"
    assert workflow["task_history"] == [
        {"task_id": "ISSUE-12-R1", "source_revision": "sha256:r2"},
        {"task_id": "ISSUE-12", "source_revision": "sha256:r1"},
    ]


def test_workflow_projection_exposes_ready_to_fix_as_approval_state(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(13),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.triage.queued", actor="kernel", task_id="ISSUE-13",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:r",
                 "workflow_run_id": "TRIAGE-13", "status": "triage_queued"},
    )
    writer.emit(
        "workflow.invoke.accepted", actor="kernel", task_id="ISSUE-13",
        payload={"workflow_run_id": "TRIAGE-13"},
    )
    writer.emit(
        "external_issue.triage.ready_to_fix", actor="kernel", task_id="ISSUE-13",
        payload={"source_key": mirror.issue_key, "workflow_run_id": "TRIAGE-13"},
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    projection = TestClient(app).get(
        "/api/projects/test/issue-triage/13"
    ).json()["issue"]["workflow"]
    assert projection["state"] == "awaiting_fix_approval"


def test_fix_queue_is_not_regressed_by_replayed_delivery_proposal(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(14),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-14",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-14",
        },
    )
    proposal = {
        "proposal": {
            "proposal_id": "delivery-proposal-1",
            "payload": {"route_id": "delivery:issue:default"},
        },
    }
    writer.emit("operator.action.proposed", actor="web", task_id="ISSUE-14", payload=proposal)
    writer.emit(
        "workflow.invoke.requested", actor="web", task_id="ISSUE-14",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-14"},
    )
    writer.emit("operator.action.proposed", actor="web", task_id="ISSUE-14", payload={
        "proposal": {
            "proposal_id": "delivery-proposal-duplicate",
            "payload": {"route_id": "delivery:issue:default"},
        },
    })

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    workflow = TestClient(app).get(
        "/api/projects/test/issue-triage/14"
    ).json()["issue"]["workflow"]
    assert workflow["state"] == "fix_queued"
    assert workflow["run_id"] == "FIX-14"


def test_fix_acceptance_does_not_regress_to_triaging(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(15),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.triage.queued", actor="kernel", task_id="ISSUE-15",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision-15",
            "workflow_run_id": "TRIAGE-15",
        },
    )
    writer.emit(
        "workflow.invoke.accepted", actor="kernel", task_id="ISSUE-15",
        payload={
            "flow_kind": "workflow",
            "workflow_run_id": "TRIAGE-15",
            "workflow_id": "external-issue-triage/v1",
        },
    )
    writer.emit(
        "workflow.invoke.requested", actor="web", task_id="ISSUE-15",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-15"},
    )
    writer.emit(
        "workflow.invoke.accepted", actor="kernel", task_id="ISSUE-15",
        payload={
            "flow_kind": "issue",
            "workflow_run_id": "FIX-15",
            "workflow_id": "external-issue-fix/v1",
        },
    )
    # A late/replayed triage acceptance must not overwrite the accepted Fix.
    writer.emit(
        "workflow.invoke.accepted", actor="kernel", task_id="ISSUE-15",
        payload={
            "flow_kind": "workflow",
            "workflow_run_id": "FIX-15",
            "workflow_id": "external-issue-triage/v1",
        },
    )

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    workflow = TestClient(app).get(
        "/api/projects/test/issue-triage/15",
    ).json()["issue"]["workflow"]
    assert workflow["state"] == "fix_queued"
    assert workflow["run_id"] == "FIX-15"


def test_fix_child_dispatch_can_match_parent_run_identity(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(16),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.triage.queued", actor="kernel", task_id="ISSUE-16",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision-16",
            "workflow_run_id": "TRIAGE-16",
        },
    )
    writer.emit(
        "workflow.invoke.requested", actor="web", task_id="ISSUE-16",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-16"},
    )
    writer.emit(
        "fanout.child.dispatched", actor="kernel", task_id="WORKER-16",
        correlation_id="FIX-16",
        payload={
            "parent_task_id": "ISSUE-16",
            "workflow_run_id": "FIX-16",
        },
    )

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    workflow = TestClient(app).get(
        "/api/projects/test/issue-triage/16",
    ).json()["issue"]["workflow"]
    assert workflow["state"] == "fixing"


def test_issue_projection_replays_from_a_fresh_context_for_each_transition(
    tmp_path: Path,
) -> None:
    """A restarted Web/kernel context must derive the same state from the ledger."""
    state_dir = tmp_path / "state"
    initial = configured_context(state_dir)
    mirror, body = normalize_github_issue(
        issue_payload(18),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(initial.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.triage.queued", actor="kernel", task_id="ISSUE-18",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision-18",
            "workflow_run_id": "TRIAGE-18",
        },
    )
    writer.emit(
        "workflow.invoke.requested", actor="web", task_id="ISSUE-18",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-18"},
    )
    writer.emit(
        "fanout.child.dispatched", actor="kernel", task_id="WORKER-18",
        correlation_id="FIX-18",
        payload={"parent_task_id": "ISSUE-18", "workflow_run_id": "FIX-18"},
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda _project_id: configured_context(state_dir),
    ))
    client = TestClient(app)
    assert client.get("/api/projects/test/issue-triage/18").json()["issue"]["workflow"]["state"] == "fixing"

    writer.emit(
        "candidate.ready", actor="zf-kernel", correlation_id="FIX-18",
        payload={
            "workflow_run_id": "FIX-18",
            "flow_kind": "issue",
            "pdd_id": "PDD-18",
            "feature_id": "FEATURE-18",
        },
    )
    assert client.get("/api/projects/test/issue-triage/18").json()["issue"]["workflow"]["state"] == "verifying"
    writer.emit(
        "judge.passed", actor="judge", correlation_id="FIX-18",
        payload={
            "workflow_run_id": "FIX-18",
            "flow_kind": "issue",
            "pdd_id": "PDD-18",
            "feature_id": "FEATURE-18",
        },
    )
    assert client.get("/api/projects/test/issue-triage/18").json()["issue"]["workflow"]["state"] == "verified_candidate"



def test_list_filters_all_queued_workflow_states(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    store = IssueMirrorStore(ctx.state_dir)
    mirrors = {}
    for number in (7, 8, 9):
        mirror, body = normalize_github_issue(
            issue_payload(number),
            repository="rzwu-uisee/zaofu-self-issue",
            repository_id="123",
            seen_at="2026-08-25T02:00:00+00:00",
        )
        store.upsert(mirror, body)
        mirrors[number] = mirror

    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    for number in (7, 8):
        task_id = f"ISSUE-{number}"
        run_id = f"TRIAGE-{number}"
        writer.emit(
            "external_issue.received",
            actor="github-poller",
            payload={
                "source_key": mirrors[number].issue_key,
                "source_revision": f"sha256:revision-{number}",
            },
        )
        writer.emit(
            "external_issue.triage.queued",
            actor="external-issue-intake",
            task_id=task_id,
            payload={
                "source_key": mirrors[number].issue_key,
                "source_revision": f"sha256:revision-{number}",
                "workflow_run_id": run_id,
            },
        )
    writer.emit(
        "workflow.invoke.requested",
        actor="web",
        task_id="ISSUE-8",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-8"},
    )

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)

    triage_queued = client.get(
        "/api/projects/test/issue-triage",
        params={"states": json.dumps(["triage_queued"])},
    ).json()
    fix_queued = client.get(
        "/api/projects/test/issue-triage",
        params={"states": json.dumps(["fix_queued"])},
    ).json()
    both_queued_states = client.get(
        "/api/projects/test/issue-triage",
        params={"states": json.dumps(["triage_queued", "fix_queued"])},
    ).json()
    summary_states = client.get(
        "/api/projects/test/issue-triage/summary",
    ).json()["states"]

    assert [item["number"] for item in triage_queued["items"]] == [7]
    assert [item["number"] for item in fix_queued["items"]] == [8]
    assert {item["number"] for item in both_queued_states["items"]} == {7, 8}
    assert summary_states == {
        "fix_queued": 1,
        "mirrored": 1,
        "open": 3,
        "triage_queued": 1,
    }


def test_filter_requests_reuse_the_current_issue_workflow_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.web import issue_triage_routes as routes

    ctx = configured_context(tmp_path / "state")
    store = IssueMirrorStore(ctx.state_dir)
    for number in (31, 32):
        mirror, body = normalize_github_issue(
            issue_payload(number),
            repository="rzwu-uisee/zaofu-self-issue",
            repository_id="123",
            seen_at="2026-08-25T02:00:00+00:00",
        )
        store.upsert(mirror, body)
    original = routes._workflow_projection
    calls = 0

    def counted(events, item):
        nonlocal calls
        calls += 1
        return original(events, item)

    monkeypatch.setattr(routes, "_workflow_projection", counted)
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)

    assert client.get("/api/projects/test/issue-triage/summary").status_code == 200
    first_calls = calls
    assert client.get(
        "/api/projects/test/issue-triage",
        params={"states": json.dumps(["open"])},
    ).status_code == 200

    assert first_calls == 2
    assert calls == first_calls


def test_workflow_projection_leaves_triaging_after_terminal_success(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.received", actor="github-poller",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:revision"},
    )
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-123",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
        },
    )
    writer.emit(
        "workflow.invoke.accepted", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123"},
    )
    writer.emit(
        "run.goal.completed", actor="zf-cli", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123", "run_id": "TRIAGE-123"},
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    workflow = TestClient(app).get(
        "/api/projects/test/issue-triage/7"
    ).json()["issue"]["workflow"]
    assert workflow["state"] == "awaiting_fix_approval"


def test_workflow_projection_tracks_pause_resume_and_fix_cancellation(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.received", actor="github-poller",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:revision"},
    )
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-123",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
        },
    )
    writer.emit(
        "workflow.invoke.accepted", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123", "workflow_id": "external-issue-triage/v1"},
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "triaging"
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["workflow_id"] == "external-issue-triage/v1"

    writer.emit(
        "run.paused", actor="web", task_id="ISSUE-123",
        payload={"run_id": "TRIAGE-123", "workflow_run_id": "TRIAGE-123"},
    )
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "triage_paused"
    writer.emit(
        "run.resumed", actor="web", task_id="ISSUE-123",
        payload={"run_id": "TRIAGE-123", "workflow_run_id": "TRIAGE-123"},
    )
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "triaging"

    writer.emit(
        "workflow.invoke.requested", actor="web", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-123"},
    )
    writer.emit(
        "task.dispatched", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "FIX-123"},
    )
    writer.emit(
        "run.paused", actor="web", task_id="ISSUE-123",
        payload={"run_id": "FIX-123", "workflow_run_id": "FIX-123"},
    )
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "fix_paused"
    writer.emit(
        "run.resumed", actor="web", task_id="ISSUE-123",
        payload={"run_id": "FIX-123", "workflow_run_id": "FIX-123"},
    )
    writer.emit(
        "candidate.ready", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "FIX-123"},
    )
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "verifying"
    writer.emit(
        "run.cancelled", actor="web", task_id="ISSUE-123",
        payload={"run_id": "FIX-123", "workflow_run_id": "FIX-123"},
    )
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "fix_cancelled"


def test_workflow_projection_retries_after_dynamic_verify_failure(tmp_path: Path) -> None:
    """A failed candidate verification enters recovery, then a new Fix run
    must produce a new candidate before the Issue can become verified.

    This replay is intentionally separate from the generic run failure tests:
    it binds both Fix generations to the same Issue task and proves that the
    stale first candidate cannot satisfy the second verification.
    """
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.received", actor="github-poller",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:revision"},
    )
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-123",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
        },
    )
    writer.emit(
        "workflow.invoke.accepted", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123", "flow_kind": "workflow"},
    )
    writer.emit(
        "run.goal.completed", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123"},
    )
    writer.emit(
        "workflow.invoke.requested", actor="web", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-1"},
    )
    writer.emit(
        "workflow.invoke.accepted", actor="zf-kernel", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-1"},
    )
    writer.emit(
        "task.dispatched", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "FIX-1"},
    )
    writer.emit(
        "candidate.ready", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "FIX-1", "pdd_id": "candidate-1"},
    )

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "verifying"

    writer.emit(
        "task.pipeline.verify.failed", actor="zf-kernel", task_id="ISSUE-123",
        payload={
            "workflow_run_id": "FIX-1",
            "operation_id": "op-verify-1",
            "reason": "dynamic command failed",
        },
    )
    failed = client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]
    assert failed["state"] == "fix_recovering"
    assert failed["failure_event"] == "task.pipeline.verify.failed"
    assert failed["failure_reason"] == "dynamic command failed"

    writer.emit(
        "workflow.invoke.requested", actor="run-manager", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-2"},
    )
    writer.emit(
        "workflow.invoke.accepted", actor="zf-kernel", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-2"},
    )
    writer.emit(
        "task.dispatched", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "FIX-2"},
    )
    writer.emit(
        "candidate.ready", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "FIX-2", "pdd_id": "candidate-2"},
    )
    writer.emit(
        "judge.passed", actor="judge", task_id="ISSUE-123",
        payload={"workflow_run_id": "FIX-2", "pdd_id": "candidate-2"},
    )
    verified = client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]
    assert verified["state"] == "verified_candidate"
    assert verified["failure_event"] == ""
    assert verified["failure_reason"] == ""


def test_stage_failure_does_not_make_active_triage_run_restartable(tmp_path: Path) -> None:
    """A stage failure can trigger recovery while the parent Run is active."""
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.received", actor="github-poller",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:revision"},
    )
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-123",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
        },
    )
    writer.emit(
        "workflow.invoke.accepted", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123"},
    )
    writer.emit(
        "external-issue-triage-verify.failed", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123", "reason": "attempt failed; recovery pending"},
    )

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)

    active = client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]
    assert active["state"] == "triage_recovering"
    assert active["run_id"] == "TRIAGE-123"
    assert active["failure_event"] == "external-issue-triage-verify.failed"
    assert active["failure_reason"] == "attempt failed; recovery pending"

    writer.emit(
        "run.goal.blocked", actor="run-manager",
        payload={
            "workflow_run_id": "TRIAGE-123",
            "reason": "unrecoverable_human_escalation",
        },
        correlation_id="TRIAGE-123",
    )
    # Cleanup/lease failures can arrive after terminalization. They must not
    # reopen the Run as recovering.
    writer.emit(
        "task.attempt.failed", actor="zf-kernel", task_id="ISSUE-123",
        payload={"workflow_run_id": "TRIAGE-123", "reason": "lease_expired"},
    )
    terminal = client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]
    assert terminal["state"] == "triage_blocked"
    assert terminal["failure_event"] == "run.goal.blocked"
    assert terminal["failure_reason"] == "unrecoverable_human_escalation"


def test_fix_dispatch_recovery_is_visible_until_worker_dispatches(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.received", actor="github-poller",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:revision"},
    )
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-123",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
        },
    )
    writer.emit(
        "workflow.invoke.requested", actor="web-operator", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-123"},
    )
    writer.emit(
        "fanout.child.dispatch_deferred", actor="zf-cli",
        correlation_id="FIX-123",
        payload={
            "run_id": "FIX-123",
            "role_instance": "issue-fix-lane-0",
            "reason": (
                "worker_transport_not_alive; "
                "worker_state_not_dispatchable:respawning"
            ),
        },
    )

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)

    recovering = client.get(
        "/api/projects/test/issue-triage/7"
    ).json()["issue"]["workflow"]
    assert recovering["state"] == "fix_recovering"
    assert recovering["failure_event"] == "fanout.child.dispatch_deferred"
    assert recovering["failure_reason"] == (
        "worker_transport_not_alive; worker_state_not_dispatchable:respawning"
    )

    writer.emit(
        "fanout.child.dispatched", actor="zf-cli", correlation_id="FIX-123",
        payload={
            "run_id": "FIX-123",
            "parent_task_id": "ISSUE-123",
            "role_instance": "issue-fix-lane-0",
        },
    )
    fixing = client.get(
        "/api/projects/test/issue-triage/7"
    ).json()["issue"]["workflow"]
    assert fixing["state"] == "fixing"
    assert fixing["failure_event"] == ""
    assert fixing["failure_reason"] == ""


def test_busy_fix_worker_remains_queued_without_false_recovery(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.received", actor="github-poller",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:revision"},
    )
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-123",
        payload={
            "source_key": mirror.issue_key,
            "source_revision": "sha256:revision",
            "workflow_run_id": "TRIAGE-123",
        },
    )
    writer.emit(
        "workflow.invoke.requested", actor="web-operator", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-123"},
    )
    writer.emit(
        "fanout.child.dispatch_deferred", actor="zf-cli",
        correlation_id="FIX-123",
        payload={
            "run_id": "FIX-123",
            "role_instance": "issue-fix-lane-0",
            "reason": "worker_state_not_dispatchable:busy",
        },
    )

    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)
    queued = client.get(
        "/api/projects/test/issue-triage/7"
    ).json()["issue"]["workflow"]
    assert queued["state"] == "fix_queued"
    assert queued["failure_event"] == ""
    assert queued["failure_reason"] == ""


def test_workflow_projection_tracks_candidate_delivery_lifecycle(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "external_issue.received", actor="github-poller",
        payload={"source_key": mirror.issue_key, "source_revision": "sha256:revision"},
    )
    writer.emit(
        "external_issue.triage.queued", actor="external-issue-intake",
        task_id="ISSUE-123",
        payload={"source_key": mirror.issue_key, "workflow_run_id": "TRIAGE-123"},
    )
    writer.emit(
        "workflow.invoke.requested", actor="web-operator", task_id="ISSUE-123",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-123"},
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)
    writer.emit(
        "candidate.ready", actor="zf-kernel", correlation_id="FIX-123",
        payload={
            "workflow_run_id": "FIX-123",
            "flow_kind": "issue",
            "pdd_id": "PDD-123",
            "feature_id": "FEATURE-123",
        },
    )
    assert client.get(
        "/api/projects/test/issue-triage/7"
    ).json()["issue"]["workflow"]["state"] == "verifying"
    writer.emit(
        "judge.passed", actor="judge", correlation_id="FIX-123",
        payload={
            "workflow_run_id": "FIX-123",
            "flow_kind": "issue",
            "pdd_id": "PDD-123",
            "feature_id": "FEATURE-123",
        },
    )
    assert client.get(
        "/api/projects/test/issue-triage/7"
    ).json()["issue"]["workflow"]["state"] == "verified_candidate"
    writer.emit(
        "candidate.owner_review.approved", actor="web-operator", task_id="ISSUE-123",
        payload={"source_key": mirror.issue_key},
    )
    writer.emit(
        "candidate.publication.prepared", actor="zf-kernel", task_id="ISSUE-123",
        payload={"source_key": mirror.issue_key},
    )
    writer.emit(
        "forge.pull_request.recorded", actor="web-operator", task_id="ISSUE-123",
        payload={"source_key": mirror.issue_key, "status": "pr_open"},
    )
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "pr_open"
    writer.emit(
        "delivery.merged", actor="github-pr-reader", task_id="ISSUE-123",
        payload={"source_key": mirror.issue_key, "status": "merged"},
    )
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "merged"


def test_full_chain_replay_keeps_one_evidence_binding_across_delivery_stages(
    tmp_path: Path,
) -> None:
    """Replay one issue through triage, fix, verify, PR, review and merge.

    The projection test above proves state transitions independently.  This
    companion test proves that the transitions can be audited as one chain:
    every stage carries the same immutable source revision, candidate digest,
    and remote test marker, while the PR/merge refs remain explicit.  It is
    provider-neutral and deliberately does not perform remote writes.
    """

    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(),
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    marker = "e2e-marker-issue-7"
    binding = {
        "source_key": mirror.issue_key,
        "source_revision": "sha256:source-e2e-7",
        "candidate_digest": "sha256:candidate-e2e-7",
        "e2e_marker": marker,
    }

    stages = [
        ("external_issue.received", "github-poller", ""),
        ("external_issue.triage.queued", "external-issue-intake", "TRIAGE-E2E-7"),
        ("workflow.invoke.requested", "web-operator", "FIX-E2E-7"),
        ("candidate.ready", "zf-kernel", "FIX-E2E-7"),
        ("judge.passed", "judge", "FIX-E2E-7"),
        ("candidate.owner_review.approved", "web-operator", "ISSUE-123"),
        ("candidate.publication.prepared", "zf-kernel", "ISSUE-123"),
        ("forge.pull_request.recorded", "web-operator", "ISSUE-123"),
        ("delivery.merged", "github-pr-reader", "ISSUE-123"),
    ]
    for event_type, actor, task_id in stages:
        payload = dict(binding)
        if event_type == "workflow.invoke.requested":
            payload.update({"flow_kind": "issue", "workflow_run_id": task_id})
        elif event_type in {"candidate.ready", "judge.passed"}:
            payload.update({"flow_kind": "issue", "workflow_run_id": task_id})
        elif event_type == "external_issue.triage.queued":
            payload["workflow_run_id"] = task_id
        elif event_type == "forge.pull_request.recorded":
            payload.update({
                "status": "pr_open",
                "remote_pr_url": "https://github.com/rzwu-uisee/zaofu-self-issue/pull/7",
                "remote_pr_head_sha": "a" * 40,
                "remote_pr_base_sha": "b" * 40,
            })
        elif event_type == "delivery.merged":
            payload.update({
                "status": "merged",
                "remote_merge_commit": "c" * 40,
            })
        writer.emit(event_type, actor=actor, task_id=task_id, payload=payload)

    events = writer.event_log.read_all()
    chain = [event for event in events if event.payload.get("e2e_marker") == marker]
    assert [event.type for event in chain] == [event_type for event_type, _, _ in stages]
    assert {
        (event.payload.get("source_revision"), event.payload.get("candidate_digest"), event.payload.get("e2e_marker"))
        for event in chain
    } == {(binding["source_revision"], binding["candidate_digest"], marker)}
    pr_event = next(event for event in chain if event.type == "forge.pull_request.recorded")
    merge_event = next(event for event in chain if event.type == "delivery.merged")
    assert pr_event.payload["remote_pr_url"].endswith("/pull/7")
    assert pr_event.payload["remote_pr_head_sha"] == "a" * 40
    assert pr_event.payload["remote_pr_base_sha"] == "b" * 40
    assert merge_event.payload["remote_merge_commit"] == "c" * 40


@pytest.mark.parametrize("lane", ["triage", "fix"])
@pytest.mark.parametrize("condition", ["local", "remote_only", "cancelled", "wrong_id", "lane_mismatch"])
def test_cancel_queued_route_requires_current_local_issue_queue(
    tmp_path: Path, lane: str, condition: str,
) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(
        issue_payload(labels=(f"{lane}_queued",)),
        repository="rzwu-uisee/zaofu-self-issue", repository_id="123",
        seen_at="2026-08-25T02:00:00+00:00",
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    local_lane = ("fix" if lane == "triage" else "triage") if condition == "lane_mismatch" else lane
    run_id = "TRIAGE-123"
    if condition != "remote_only":
        writer.emit("external_issue.triage.queued", actor="kernel", task_id="ISSUE-123", payload={
            "source_key": mirror.issue_key, "workflow_run_id": run_id, "triage_attempt": 2,
        })
        if local_lane == "fix":
            run_id = "FIX-123"
            writer.emit("workflow.invoke.requested", actor="kernel", task_id="ISSUE-123", payload={
                "source_key": mirror.issue_key, "workflow_run_id": run_id, "flow_kind": "issue",
            })
        if condition == "cancelled":
            writer.emit(f"external_issue.{lane}.queued_cancelled", actor="kernel", task_id="ISSUE-123", payload={
                "source_key": mirror.issue_key, "workflow_run_id": run_id,
            })
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda _: ctx, mutation_auth_error=lambda *a, **kw: None,
    ))
    client = TestClient(app)
    url = "/api/projects/test/issue-triage/7"
    workflow = client.get(url).json()["issue"]["workflow"]
    assert workflow["state"] == f"{lane}_{'cancelled' if condition == 'cancelled' else 'queued'}"
    assert workflow["queued_cancel_available"] is (condition in {"local", "wrong_id"})
    before = writer.event_log.read_all()
    response = client.post(url + "/cancel-queued", json={"triage_id": "OTHER-ISSUE-RUN"} if condition == "wrong_id" else {})
    if condition == "local":
        assert response.status_code == 200, response.text
        assert response.json()["triage_id"] == run_id
        assert response.json()["status"] == "cancelled"
        cancelled = [e for e in writer.event_log.read_all() if e.type == f"external_issue.{lane}.queued_cancelled"]
        assert len(cancelled) == 1 and cancelled[0].payload["workflow_run_id"] == run_id
        # A lagging remote label must not make a terminal local request cancellable again.
        assert client.get(url).json()["issue"]["workflow"]["queued_cancel_available"] is False
        assert client.get(url).json()["issue"]["workflow"]["state"] == f"{lane}_cancelled"
    else:
        assert response.status_code == 409, response.text
        assert response.json()["status"] == ("revision_conflict" if condition == "wrong_id" else "invalid_transition" if condition == "cancelled" else "not_cancellable")
        assert "triage_id is required" not in response.text
        assert writer.event_log.read_all() == before


@pytest.mark.parametrize("lane", ["triage", "fix"])
def test_cross_project_queue_cancel_and_terminal_projection(tmp_path: Path, lane: str) -> None:
    contexts = {name: configured_context(tmp_path / name) for name in ("viewer", "owner")}
    mirror, body = normalize_github_issue(
        issue_payload(labels=(f"{lane}_queued",)), repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123", seen_at="2026-08-25T02:00:00+00:00",
    )
    for ctx in contexts.values():
        IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(contexts["owner"].state_dir / "events.jsonl"))
    writer.emit("external_issue.triage.queued", actor="kernel", task_id="ISSUE-123", payload={
        "source_key": mirror.issue_key, "workflow_run_id": "TRIAGE-123",
    })
    if lane == "fix":
        writer.emit("workflow.invoke.requested", actor="kernel", task_id="ISSUE-123", payload={
            "source_key": mirror.issue_key, "workflow_run_id": "FIX-123", "flow_kind": "issue",
        })
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=contexts.__getitem__, project_ids=lambda: list(contexts),
        mutation_auth_error=lambda *a, **kw: None,
    ))
    client = TestClient(app)
    url = "/api/projects/viewer/issue-triage/7"
    projected = client.get(url).json()["issue"]["workflow"]
    assert projected["workflow_project_id"] == "owner"
    assert projected["queued_cancel_available"] is True
    assert client.post(url + "/cancel-queued", json={"triage_id": "UNRELATED"}).status_code == 409
    result = client.post(url + "/cancel-queued", json={})
    assert result.status_code == 200, result.text
    assert result.json()["workflow_project_id"] == "owner"
    for name in contexts:
        base = f"/api/projects/{name}/issue-triage"
        assert client.get(base + "/7").json()["issue"]["workflow"]["state"] == f"{lane}_cancelled"
        assert client.get(base).json()["items"][0]["workflow"]["state"] == f"{lane}_cancelled"
        assert client.get(base + "/summary").json()["states"][f"{lane}_cancelled"] == 1
    assert not EventLog(contexts["viewer"].state_dir / "events.jsonl").read_all()


@pytest.mark.parametrize("suffix", ["", "/summary"])
def test_cross_project_projection_reads_each_project_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    from collections import Counter
    from unittest.mock import Mock
    from zf.web import issue_triage_routes as routes

    contexts = {name: configured_context(tmp_path / name) for name in ("viewer", "owner")}
    for number in range(1, 13):
        mirror, body = normalize_github_issue(
            issue_payload(number), repository="rzwu-uisee/zaofu-self-issue",
            repository_id="123", seen_at="2026-08-25T02:00:00+00:00",
        )
        for ctx in contexts.values():
            IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(contexts["owner"].state_dir / "events.jsonl"))
    writer.emit("external_issue.triage.queued", actor="kernel", task_id="ISSUE-12", payload={
        "source_key": mirror.issue_key, "workflow_run_id": "TRIAGE-12",
    })
    registry = Mock(return_value=list(contexts))
    manifests = Mock(wraps=routes.build_event_manifest)
    reads: Counter[str] = Counter()
    original_list = IssueMirrorStore.list

    def counted_list(store):
        reads[str(store.state_dir)] += 1
        return original_list(store)

    monkeypatch.setattr(IssueMirrorStore, "list", counted_list)
    monkeypatch.setattr(routes, "build_event_manifest", manifests)
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=contexts.__getitem__, project_ids=registry))
    client = TestClient(app)
    for _ in range(2):  # Cold and warm projection caches have the same bounded I/O.
        reads.clear()
        manifests.reset_mock()
        registry.reset_mock()
        response = client.get("/api/projects/viewer/issue-triage" + suffix)
        assert response.status_code == 200, response.text
        assert reads == {str(ctx.state_dir): 1 for ctx in contexts.values()}
        assert manifests.call_count == 2
        registry.assert_called_once_with()
        if suffix:
            assert response.json()["states"]["triage_queued"] == 1
        else:
            item = next(item for item in response.json()["items"] if item["number"] == 12)
            assert item["workflow"]["workflow_project_id"] == "owner"


def test_queued_triage_cancel_is_idempotent_and_event_backed(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    mirror, body = normalize_github_issue(issue_payload(), repository="rzwu-uisee/zaofu-self-issue", repository_id="123", seen_at="2026-08-25T02:00:00+00:00")
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit("external_issue.triage.queued", actor="kernel", task_id="ISSUE-123", payload={
        "source_key": mirror.issue_key, "source_revision": "sha256:r", "workflow_run_id": "TRIAGE-123",
        "triage_attempt": 2,
    })
    requested = writer.emit("external_issue.triage.queued_cancel_requested", actor="web", task_id="ISSUE-123", payload={})
    service = ControlledActionService(ctx.state_dir, writer, config=ctx.config, project_root=ctx.state_dir.parent)
    first = service.execute(action="external-issue-triage-queued-cancel", requested_action="cancel-queued",
                            payload={"triage_id": "TRIAGE-123", "expected_revision": 2}, requested=requested)
    second_requested = writer.emit("external_issue.triage.queued_cancel_requested", actor="web", task_id="ISSUE-123", payload={})
    second = service.execute(action="external-issue-triage-queued-cancel", requested_action="cancel-queued",
                             payload={"triage_id": "TRIAGE-123", "expected_revision": 2}, requested=second_requested)
    assert first["status"] == second["status"] == "cancelled"
    assert second["idempotent_replay"] is True


@pytest.mark.parametrize('received', [False, True])
@pytest.mark.parametrize(('transition', 'expected_state', 'outcome'), [
    ('workflow.invoke.accepted', 'triaging', 'active'),
    ('external_issue.triage.queued_cancelled', 'triage_cancelled', 'attention'),
])
def test_manual_intake_without_received_event_keeps_repository_completion(
    tmp_path: Path, received: bool, transition: str, expected_state: str, outcome: str,
) -> None:
    ctx = configured_context(tmp_path / 'state')
    mirror, body = normalize_github_issue(
        issue_payload(37), repository='rzwu-uisee/zaofu-self-issue',
        repository_id='123', seen_at='2026-09-09T02:00:00+00:00',
    )
    IssueMirrorStore(ctx.state_dir).upsert(mirror, body)
    writer = EventWriter(EventLog(ctx.state_dir / 'events.jsonl'))
    if received:
        writer.emit('external_issue.received', actor='test', payload={'source_key': mirror.issue_key})
    writer.emit('external_issue.triage.queued', actor='test', task_id='TASK-37', payload={
        'source_key': mirror.issue_key, 'workflow_run_id': 'TRIAGE-37',
    })
    writer.emit(transition, actor='test', task_id='TASK-37', payload={
        'workflow_run_id': 'TRIAGE-37',
    })
    # A different Issue's successful handoff must not turn this one's Triage green.
    writer.emit('external_issue.triage.ready_to_fix', actor='test', task_id='OTHER', payload={
        'source_key': 'github:other:999', 'workflow_run_id': 'TRIAGE-OTHER',
    })
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    value = TestClient(app).get('/api/projects/test/issue-triage/37').json()['issue']
    lifecycle = value['workflow']['lifecycle']
    assert lifecycle['current'] == 'triage'
    assert lifecycle['stages']['repository']['outcome'] == 'completed'
    assert lifecycle['stages']['repository']['state'] == 'open'
    assert lifecycle['stages']['triage']['state'] == expected_state
    assert lifecycle['stages']['triage']['outcome'] == outcome
    assert all(lifecycle['stages'][stage]['outcome'] == 'unknown' for stage in ('decision', 'fix', 'delivery', 'complete'))
    assert value['created_at'] == '2026-08-25T00:00:00Z'


def test_reconciler_fetches_comments_concurrently_and_keeps_all_records(tmp_path: Path) -> None:
    from threading import Barrier

    rendezvous = Barrier(4, timeout=5)

    def transport(method: str, url: str, headers: dict[str, str]):
        if url.endswith('/repos/rzwu-uisee/zaofu-self-issue'):
            return 200, json.dumps({'id': 123, 'full_name': 'rzwu-uisee/zaofu-self-issue'}).encode(), {}
        if '/comments?' in url:
            rendezvous.wait()
            return 200, json.dumps([comment_payload()]).encode(), {}
        return 200, json.dumps([issue_payload(number) for number in range(1, 5)]).encode(), {}

    reconciler = GitHubIssueReconciler(tmp_path / 'state', 'rzwu-uisee/zaofu-self-issue', transport=transport)
    result = reconciler.refresh()
    assert result['ok'] is True
    assert len(reconciler.store.list()) == 4
    for mirror in reconciler.store.list():
        assert reconciler.store.read_comments(mirror)[0].author_login == 'commenter'


def test_attachment_proxy_supports_release_media_octet_type_and_ranges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from zf.web.issue_triage_routes import build_issue_triage_router
    ctx = configured_context(tmp_path / "state")
    class Response:
        headers = {"Content-Type": "application/octet-stream"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return b"\x1a\x45\xdf\xa3video"
    monkeypatch.setattr("zf.web.issue_triage_routes.urllib.request.urlopen", lambda request, timeout: Response())
    app = FastAPI(); app.include_router(build_issue_triage_router(resolve_ctx=lambda _: ctx))
    client = TestClient(app)
    url = "https://github.com/rzwu-uisee/zaofu-self-issue/releases/download/evidence/video.webm"
    response = client.get("/api/projects/test/issue-triage/attachment", params={"url": url}, headers={"Range":"bytes=0-3"})
    assert response.status_code == 206
    assert response.headers["content-type"] == "video/webm"
    assert response.headers["content-range"] == "bytes 0-3/9"
    assert response.content == b"\x1a\x45\xdf\xa3"
