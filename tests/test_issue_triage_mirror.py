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
from zf.web.issue_triage_routes import build_issue_triage_router
from zf.web.server import create_app


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
    ))
    client = TestClient(app)
    assert client.post(
        "/api/projects/test/issue-triage/refresh?force=true",
    ).status_code == 200
    assert force_values == [True]


def test_manual_start_triage_admits_one_historical_issue(tmp_path: Path) -> None:
    ctx = configured_context(tmp_path / "state")
    ctx.state_dir.mkdir(parents=True)
    (ctx.state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    ctx.config.self_issue.ingress = ExternalIssueIngressConfig(enabled=True)
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

    assert [item["number"] for item in triage_queued["items"]] == [7]
    assert [item["number"] for item in fix_queued["items"]] == [8]
    assert {item["number"] for item in both_queued_states["items"]} == {7, 8}


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
        payload={"workflow_run_id": "TRIAGE-123"},
    )
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda project_id: ctx))
    client = TestClient(app)
    assert client.get("/api/projects/test/issue-triage/7").json()["issue"]["workflow"]["state"] == "triaging"

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
