from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ExternalIssueDeliveryConfig,
    SelfIssueConfig,
    ZfConfig,
)
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.integrations.forge.github_issues import normalize_github_issue
from zf.runtime.issue_candidate_delivery import (
    CandidateDeliveryError,
    GitHubPullRequestReader,
    IssueCandidateDeliveryService,
)
from zf.runtime.self_issue_target import zaofu_v7_root


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _repo(root: Path) -> tuple[str, str]:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", "candidate/PDD-17")
    (root / "fix.py").write_text("fixed = True\n", encoding="utf-8")
    _git(root, "add", "fix.py")
    _git(root, "commit", "-q", "-m", "fix issue 17")
    return base, _git(root, "rev-parse", "HEAD")


def _config(state_dir: Path, *, target_locked: bool = False) -> ZfConfig:
    config = ZfConfig()
    config.project.state_dir = str(state_dir)
    config.self_issue = SelfIssueConfig(
        enabled=True,
        target_locked=target_locked,
        target_project="rzwu-uisee/zaofu-self-issue",
        delivery=ExternalIssueDeliveryConfig(
            enabled=True,
            provider="github",
            repository="rzwu-uisee/zaofu-self-issue",
            remote_url="https://github.com/rzwu-uisee/zaofu-self-issue.git",
            base_branch="dev",
            branch_prefix="review",
            merge_strategy="squash",
        ),
    )
    return config


def _issue():
    value = {
        "number": 17,
        "node_id": "I_17",
        "html_url": "https://github.com/rzwu-uisee/zaofu-self-issue/issues/17",
        "title": "Fix the workflow",
        "body": "Observed failure",
        "user": {"login": "reporter", "avatar_url": "https://github.com/reporter.png"},
        "state": "open",
        "created_at": "2026-08-31T00:00:00Z",
        "updated_at": "2026-08-31T01:00:00Z",
        "closed_at": None,
        "labels": [],
        "assignees": [],
        "comments": 0,
        "milestone": None,
    }
    return normalize_github_issue(
        value,
        repository="rzwu-uisee/zaofu-self-issue",
        repository_id="123",
        seen_at="2026-08-31T01:01:00Z",
    )[0]


def _events(state_dir: Path, *, base: str, head: str) -> None:
    writer = EventWriter(event_log_from_project(state_dir))
    writer.emit(
        "external_issue.received",
        actor="github-poller",
        payload={"source_key": "github:123:17", "source_revision": "sha256:source-1"},
    )
    writer.emit(
        "external_issue.triage.queued",
        actor="external-issue-intake",
        task_id="ISSUE-17",
        payload={
            "source_key": "github:123:17",
            "source_revision": "sha256:source-1",
            "workflow_run_id": "TRIAGE-17",
        },
    )
    writer.emit(
        "workflow.invoke.requested",
        actor="web-operator",
        task_id="ISSUE-17",
        payload={"flow_kind": "issue", "workflow_run_id": "FIX-17"},
    )
    writer.emit(
        "candidate.ready",
        actor="zf-kernel",
        correlation_id="FIX-17",
        payload={
            "workflow_run_id": "FIX-17",
            "flow_kind": "issue",
            "pdd_id": "PDD-17",
            "feature_id": "FEATURE-17",
            "candidate_ref": "candidate/PDD-17",
            "candidate_base_commit": base,
            "candidate_head_commit": head,
            "diff_ref": f"{base}..{head}",
            "quality_status": "passed",
            "quality_gates_passed": ["focused-tests"],
        },
    )
    writer.emit(
        "judge.passed",
        actor="judge",
        correlation_id="FIX-17",
        payload={
            "workflow_run_id": "FIX-17",
            "flow_kind": "issue",
            "pdd_id": "PDD-17",
            "feature_id": "FEATURE-17",
            "unresolved_risks": ["Human review required"],
        },
    )
    manifest = state_dir / "candidates" / "PDD-17" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "included_tasks": [{"task_id": "ISSUE-17", "changed_files": ["fix.py"]}],
        "quality": {"gate_checks": {"focused-tests": [{"command": "pytest -q"}]}},
    }), encoding="utf-8")


def _service(root: Path, reader: GitHubPullRequestReader | None = None):
    state_dir = root / ".zf"
    state_dir.mkdir()
    base, head = _repo(root)
    _events(state_dir, base=base, head=head)
    return (
        IssueCandidateDeliveryService(
            state_dir=state_dir,
            project_root=root,
            config=_config(state_dir),
            pr_reader=reader,
            remote_base_reader=lambda _url, _branch: base,
        ),
        state_dir,
        base,
        head,
    )


def test_locked_self_issue_delivery_uses_canonical_zaofu_repository(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    service = IssueCandidateDeliveryService(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(state_dir, target_locked=True),
    )

    assert service.project_root == zaofu_v7_root().resolve()


def test_owner_review_and_prepare_create_immutable_local_ref_without_push(
    tmp_path: Path,
) -> None:
    service, state_dir, base, head = _service(tmp_path)
    issue = _issue()
    (tmp_path / "README.md").write_text("user change\n", encoding="utf-8")

    reviewed = service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
        reason="Reviewed locally",
    )
    prepared = service.prepare(
        issue,
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    reviewed_again = service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
        reason="Reviewed locally",
    )
    prepared_again = service.prepare(
        issue,
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )

    branch = f"review/github-issue-17-{head[:8]}"
    assert reviewed["status"] == "approved_for_pr"
    assert reviewed_again["idempotent"] is True
    assert prepared["status"] == "publication_prepared"
    assert prepared["verified_remote_base_sha"] == base
    assert prepared_again["idempotent"] is True
    assert prepared["review_branch"] == branch
    assert _git(tmp_path, "rev-parse", branch) == head
    assert "rzwu-uisee/zaofu-self-issue.git" in prepared["human_commands"]["push"]
    assert "--base dev" in prepared["human_commands"]["create_pr"]
    status = _git(tmp_path, "status", "--porcelain")
    assert "M README.md" in status
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "user change\n"
    assert _git(tmp_path, "for-each-ref", "--format=%(refname)", "refs/remotes") == ""
    handoff = json.loads((
        state_dir / "artifacts/issue-triage/delivery/github/17/handoff.json"
    ).read_text(encoding="utf-8"))
    assert handoff["candidate_head_sha"] == head
    assert handoff["changed_paths"] == ["fix.py"]
    assert handoff["verification_commands"] == ["pytest -q"]
    event_types = [event.type for event in event_log_from_project(state_dir).read_all()]
    assert event_types.count("candidate.owner_review.approved") == 1
    assert event_types.count("candidate.publication.prepared") == 1


def test_owner_review_records_changes_and_rejection_with_reasons(tmp_path: Path) -> None:
    service, state_dir, _base, head = _service(tmp_path)
    issue = _issue()

    changed = service.review(
        issue,
        verdict="changes_requested",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
        reason="Add the missing regression case",
    )
    rejected = service.review(
        issue,
        verdict="reject",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
        reason="The proposed behavior is out of scope",
    )

    assert changed["status"] == "owner_changes_requested"
    assert rejected["status"] == "owner_rejected"
    events = event_log_from_project(state_dir).read_all()
    assert events[-2].type == "candidate.owner_review.changes_requested"
    assert events[-1].type == "candidate.owner_review.declined"
    with pytest.raises(CandidateDeliveryError, match="requires a review reason"):
        service.review(
            issue,
            verdict="reject",
            expected_candidate_sha=head,
            expected_source_revision="sha256:source-1",
        )


def test_taskless_candidate_from_another_fix_run_is_not_bound_to_issue(
    tmp_path: Path,
) -> None:
    service, state_dir, _base, head = _service(tmp_path)
    writer = EventWriter(event_log_from_project(state_dir))
    writer.emit(
        "candidate.ready",
        actor="zf-kernel",
        correlation_id="FIX-OTHER",
        payload={
            "workflow_run_id": "FIX-OTHER",
            "flow_kind": "issue",
            "pdd_id": "PDD-OTHER",
            "feature_id": "FEATURE-OTHER",
            "candidate_ref": "candidate/PDD-OTHER",
            "candidate_base_commit": "a" * 40,
            "candidate_head_commit": "b" * 40,
        },
    )
    writer.emit(
        "judge.passed",
        actor="judge",
        correlation_id="FIX-OTHER",
        payload={
            "workflow_run_id": "FIX-OTHER",
            "flow_kind": "issue",
            "pdd_id": "PDD-OTHER",
        },
    )

    candidate = service.projection(_issue())["candidate"]
    assert candidate is not None
    assert candidate["workflow_run_id"] == "FIX-17"
    assert candidate["candidate_head_sha"] == head
    assert candidate["pdd_id"] == "PDD-17"


def test_later_issue_revision_does_not_invalidate_bound_candidate(tmp_path: Path) -> None:
    service, state_dir, _base, head = _service(tmp_path)
    issue = _issue()
    service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    EventWriter(event_log_from_project(state_dir)).emit(
        "external_issue.received",
        actor="github-poller",
        payload={"source_key": issue.issue_key, "source_revision": "sha256:source-2"},
    )

    projection = service.projection(issue)
    assert projection["status"] == "approved_for_pr"
    assert projection["stale_reason"] == ""
    assert projection["candidate"]["source_revision"] == "sha256:source-1"
    assert projection["candidate"]["current_source_revision"] == "sha256:source-2"

    prepared = service.prepare(
        issue,
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )

    assert prepared["status"] == "publication_prepared"


def test_previous_handoff_does_not_mark_new_verified_candidate_stale(
    tmp_path: Path,
) -> None:
    service, state_dir, base, head = _service(tmp_path)
    issue = _issue()
    service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    next_head = "b" * 40
    writer = EventWriter(event_log_from_project(state_dir))
    writer.emit(
        "candidate.ready",
        actor="zf-kernel",
        correlation_id="FIX-17",
        payload={
            "workflow_run_id": "FIX-17",
            "flow_kind": "issue",
            "pdd_id": "PDD-17-NEXT",
            "feature_id": "FEATURE-17-NEXT",
            "candidate_ref": "candidate/PDD-17-NEXT",
            "candidate_base_commit": base,
            "candidate_head_commit": next_head,
            "quality_status": "passed",
        },
    )
    writer.emit(
        "judge.passed",
        actor="judge",
        correlation_id="FIX-17",
        payload={
            "workflow_run_id": "FIX-17",
            "flow_kind": "issue",
            "pdd_id": "PDD-17-NEXT",
            "feature_id": "FEATURE-17-NEXT",
        },
    )

    projection = service.projection(issue)

    assert projection["status"] == ""
    assert projection["stale_reason"] == ""
    assert projection["candidate"]["candidate_head_sha"] == next_head


def test_prepare_fails_closed_when_remote_base_drifted(tmp_path: Path) -> None:
    service, state_dir, _base, head = _service(tmp_path)
    issue = _issue()
    service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    service.remote_base_reader = lambda _url, _branch: "b" * 40

    with pytest.raises(CandidateDeliveryError, match="remote base changed"):
        service.prepare(
            issue,
            expected_candidate_sha=head,
            expected_source_revision="sha256:source-1",
        )

    assert not (
        state_dir / "artifacts/issue-triage/delivery/github/17/pr-body.md"
    ).exists()


def test_pull_request_reader_uses_local_github_cli_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "test-token"
    real_run = subprocess.run
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def run(command, **kwargs):
        if command == ["gh", "auth", "token"]:
            return subprocess.CompletedProcess(command, 0, stdout=token + "\n", stderr="")
        return real_run(command, **kwargs)

    def transport(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        if headers.get("Authorization") != f"Bearer {token}":
            return 403, json.dumps({"message": "API rate limit exceeded"}).encode()
        if url.endswith("/reviews"):
            return 200, b"[]"
        return 200, json.dumps({
            "number": 26,
            "html_url": "https://github.com/rzwu-uisee/zaofu-self-issue/pull/26",
            "state": "open",
            "head": {
                "ref": "review/github-issue-23-8c9a46d9",
                "sha": "8" * 40,
                "repo": {"full_name": "rzwu-uisee/zaofu-self-issue"},
            },
            "base": {"ref": "dev", "sha": "b" * 40},
        }).encode()

    monkeypatch.setattr(
        "zf.runtime.issue_candidate_delivery.subprocess.run",
        run,
    )

    result = GitHubPullRequestReader(transport=transport).read(
        "rzwu-uisee/zaofu-self-issue",
        26,
    )

    assert result["number"] == 26


def test_record_and_refresh_pr_require_exact_repo_base_and_candidate(tmp_path: Path) -> None:
    state: dict[str, object] = {"merged": False, "state": "open", "merged_at": None}
    holder: dict[str, str] = {}

    def transport(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        del headers
        if url.endswith("/reviews"):
            return 200, json.dumps([{
                "state": "APPROVED", "user": {"login": "owner"},
            }]).encode()
        payload = {
            "number": 23,
            "html_url": "https://github.com/rzwu-uisee/zaofu-self-issue/pull/23",
            "state": state["state"],
            "merged": state["merged"],
            "merged_at": state["merged_at"],
            "merge_commit_sha": "f" * 40 if state["merged"] else None,
            "updated_at": "2026-08-31T02:00:00Z",
            "head": {
                "ref": holder["branch"], "sha": holder["head"],
                "repo": {"full_name": "rzwu-uisee/zaofu-self-issue"},
            },
            "base": {"ref": "dev", "sha": holder["base"]},
        }
        return 200, json.dumps(payload).encode()

    reader = GitHubPullRequestReader(transport=transport)
    service, _state_dir, base, head = _service(tmp_path, reader)
    issue = _issue()
    holder.update({"branch": f"review/github-issue-17-{head[:8]}", "head": head, "base": base})
    service.review(
        issue, verdict="approve", expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    service.prepare(
        issue, expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )

    recorded = service.record_pr(
        issue, url="https://github.com/rzwu-uisee/zaofu-self-issue/pull/23",
    )
    assert recorded["status"] == "pr_approved"
    state.update({"merged": True, "state": "closed", "merged_at": "2026-08-31T03:00:00Z"})
    merged = service.refresh_pr(issue)
    assert merged["status"] == "merged"
    assert merged["pull_request"]["merge_commit_sha"] == "f" * 40

    holder["head"] = "e" * 40
    with pytest.raises(CandidateDeliveryError, match="head_sha"):
        service.record_pr(
            issue, url="https://github.com/rzwu-uisee/zaofu-self-issue/pull/23",
        )


def test_pr_url_is_restricted_to_configured_github_repository(tmp_path: Path) -> None:
    service, _state_dir, _base, head = _service(tmp_path)
    issue = _issue()
    service.review(
        issue, verdict="approve", expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    service.prepare(
        issue, expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )

    with pytest.raises(CandidateDeliveryError, match="configured delivery target"):
        service.record_pr(issue, url="https://github.com/other/repo/pull/1")
