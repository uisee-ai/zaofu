"""S8 conformance cases for the local-only PR handoff boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.test_issue_candidate_delivery import _issue, _service
from zf.core.events.log import EventLog
from zf.runtime.issue_candidate_delivery import CandidateDeliveryError


def test_prepare_records_human_remote_commands_without_executing_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate preparation must not push, merge, or create a remote PR."""
    service, state_dir, _base, head = _service(tmp_path)
    issue = _issue()
    service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )

    calls: list[tuple[str, ...]] = []
    real_run = subprocess.run

    def observed_run(command, *args, **kwargs):  # noqa: ANN001
        argv = tuple(str(part) for part in command)
        calls.append(argv)
        if (
            argv[:2] == ("git", "push")
            or argv[:2] == ("git", "merge")
            or argv[:3] == ("gh", "pr", "create")
        ):
            raise AssertionError(f"forbidden remote side effect attempted: {argv}")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(
        "zf.runtime.issue_candidate_delivery.subprocess.run",
        observed_run,
    )

    prepared = service.prepare(
        issue,
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )

    forbidden = {
        argv
        for argv in calls
        if argv[:2] == ("git", "push")
        or argv[:2] == ("git", "merge")
        or argv[:3] == ("gh", "pr", "create")
    }
    assert not forbidden
    assert prepared["status"] == "publication_prepared"
    assert prepared["human_commands"]["push"].startswith("git push ")
    assert prepared["human_commands"]["create_pr"].startswith("gh pr create ")

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert sum(event.type == "candidate.publication.prepared" for event in events) == 1
    assert not any(event.type in {"git.pushed", "pull_request.created"} for event in events)


def test_remote_pr_lookup_failure_keeps_handoff_unpublished(
    tmp_path: Path,
) -> None:
    """A remote read failure cannot be interpreted as an open or merged PR."""

    class _UnavailablePullRequestReader:
        def read(self, _repository: str, _number: int) -> dict[str, object]:
            raise CandidateDeliveryError("GitHub pull request lookup failed: HTTP 503")

    service, state_dir, _base, head = _service(
        tmp_path,
        _UnavailablePullRequestReader(),  # type: ignore[arg-type]
    )
    issue = _issue()
    service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    prepared = service.prepare(
        issue,
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    assert prepared["status"] == "publication_prepared"

    with pytest.raises(CandidateDeliveryError, match="HTTP 503"):
        service.record_pr(
            issue,
            url="https://github.com/rzwu-uisee/zaofu-self-issue/pull/23",
        )

    handoff = (state_dir / "artifacts/issue-triage/delivery/github/17/handoff.json")
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["status"] == "publication_prepared"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not any(
        event.type in {"forge.pull_request.recorded", "delivery.merged"}
        for event in events
    )


def test_remote_pr_record_and_merge_refresh_bind_the_candidate_identity(
    tmp_path: Path,
) -> None:
    """A remote PR is accepted only when its head/base match the candidate."""
    service, state_dir, base, head = _service(tmp_path)
    issue = _issue()
    service.review(
        issue,
        verdict="approve",
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    prepared = service.prepare(
        issue,
        expected_candidate_sha=head,
        expected_source_revision="sha256:source-1",
    )
    branch = str(prepared["review_branch"])

    class _RemoteReader:
        merged = False

        def read(self, _repository: str, _number: int) -> dict[str, object]:
            return {
                "number": 23,
                "url": "https://github.com/rzwu-uisee/zaofu-self-issue/pull/23",
                "repository": "rzwu-uisee/zaofu-self-issue",
                "head_ref": branch,
                "head_sha": head,
                "base_ref": "dev",
                "base_sha": base,
                "lifecycle": "merged" if self.merged else "pr_approved",
                "merged": self.merged,
                "merged_at": "2026-09-12T01:00:00Z" if self.merged else None,
                "merge_commit_sha": "f" * 40 if self.merged else None,
            }

    reader = _RemoteReader()
    service.pr_reader = reader  # type: ignore[assignment]
    recorded = service.record_pr(
        issue,
        url="https://github.com/rzwu-uisee/zaofu-self-issue/pull/23",
    )
    assert recorded["status"] == "pr_approved"
    reader.merged = True
    merged = service.refresh_pr(issue)
    assert merged["status"] == "merged"
    assert merged["pull_request"]["merge_commit_sha"] == "f" * 40

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert sum(event.type == "forge.pull_request.recorded" for event in events) == 1
    assert sum(event.type == "delivery.merged" for event in events) == 1
