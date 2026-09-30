from __future__ import annotations

import pytest

from issue_core.enrichment import Evidence
from issue_core.investigation import (
    InvestigationBudget,
    InvestigationPlan,
    InvestigationRun,
    evidence_for_context,
)


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        plan_id="plan-1",
        draft_id="draft-1",
        draft_revision=3,
        draft_digest="sha256:" + "a" * 64,
        target_repository="owner/repo",
        project_revision="git:abc123",
        environment_fingerprint="sha256:" + "b" * 64,
        allowed_paths=("src", "tests"),
        attachment_refs=("artifact://attachment/1",),
        commands=("pytest tests/test_cli.py -q",),
    )


def test_default_investigation_budget_matches_frozen_contract() -> None:
    budget = InvestigationBudget()
    assert budget.max_rounds == 3
    assert budget.max_tools_per_round == 20
    assert budget.max_reproductions_per_round == 3
    assert budget.command_timeout_seconds == 180
    assert budget.active_runtime_seconds == 1200
    assert budget.max_no_new_evidence_rounds == 2
    assert budget.transient_retries == 2


def test_investigation_run_enforces_round_tool_and_no_evidence_limits() -> None:
    run = InvestigationRun.start("run-1", _plan(), started_at="2026-09-18T00:00:00Z")
    for _ in range(20):
        run = run.record_tool()
    with pytest.raises(ValueError, match="tool budget"):
        run.record_tool()
    for _ in range(3):
        run = run.record_reproduction()
    with pytest.raises(ValueError, match="reproduction budget"):
        run.record_reproduction()
    run = run.finish_round(new_evidence_count=0, active_seconds=300)
    assert run.round_number == 2
    run = run.finish_round(new_evidence_count=0, active_seconds=300)
    assert run.status == "stopped"
    assert run.stop_reason == "no_new_evidence"


def test_evidence_context_change_marks_artifact_stale_without_changing_integrity() -> None:
    evidence = Evidence(
        evidence_id="evidence-1",
        kind="reproduction",
        uri="artifact://run/1",
        digest="sha256:" + "c" * 64,
        collected_by="run-1",
        project_revision="git:abc123",
        captured_at="2026-09-18T00:00:00Z",
        disclosure="private",
        redaction_status="checked",
        integrity="verified",
        applicability="current",
        environment_fingerprint="sha256:" + "b" * 64,
    )
    current = evidence_for_context(
        evidence,
        project_revision="git:abc123",
        environment_fingerprint="sha256:" + "b" * 64,
    )
    stale = evidence_for_context(
        evidence,
        project_revision="git:def456",
        environment_fingerprint="sha256:" + "b" * 64,
    )
    assert current == evidence
    assert stale.integrity == "verified"
    assert stale.applicability == "stale"
    assert stale.stale_reason == "project_revision_changed"


@pytest.mark.parametrize("kind", ["attachment", "log", "source", "reproduction"])
def test_each_investigation_evidence_kind_can_be_superseded(kind: str) -> None:
    evidence = Evidence(
        evidence_id=f"{kind}-1",
        kind=kind,
        uri=f"artifact://{kind}/1",
        digest="sha256:" + "c" * 64,
        collected_by="run-1",
        project_revision="git:abc123",
        captured_at="2026-09-18T00:00:00Z",
        disclosure="private",
        redaction_status="checked",
        integrity="verified",
        applicability="current",
        environment_fingerprint="sha256:" + "b" * 64,
        candidate_digest="sha256:" + "d" * 64,
    )
    stale = evidence_for_context(
        evidence,
        project_revision="git:abc123",
        environment_fingerprint="sha256:" + "b" * 64,
        candidate_digest="sha256:" + "e" * 64,
    )
    superseded = stale.mark_superseded(f"{kind}-2")
    assert stale.applicability == "stale"
    assert stale.stale_reason == "candidate_digest_changed"
    assert superseded.integrity == "verified"
    assert superseded.applicability == "superseded"
    assert superseded.superseded_by == f"{kind}-2"
