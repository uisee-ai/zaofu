from __future__ import annotations

from pathlib import Path

from zf.autoresearch.bug_candidates import (
    candidates_from_signals,
    write_candidate_backlogs,
)
from zf.autoresearch.failure_signals import FailureSignal


def _signal(signal_id: str, fingerprint: str) -> FailureSignal:
    return FailureSignal(
        signal_id=signal_id,
        source_kind="event_log",
        source_path=".zf/events.jsonl",
        event_ids=[f"evt-{signal_id}"],
        fingerprint=fingerprint,
        category="runtime_fatal",
        severity="high",
        summary="Runtime fatal event observed",
        expected="recover",
        actual="failed",
        evidence_paths=[".zf/events.jsonl"],
    )


def test_candidates_dedupe_by_fingerprint() -> None:
    candidates = candidates_from_signals([
        _signal("a", "same"),
        _signal("b", "same"),
    ])

    assert len(candidates) == 1
    assert sorted(candidates[0].source_signals) == ["a", "b"]
    assert candidates[0].priority == "P0"


def test_write_candidate_backlogs_creates_proposed_file(tmp_path: Path) -> None:
    candidate = candidates_from_signals([_signal("a", "same")])[0]

    results = write_candidate_backlogs([candidate], out_dir=tmp_path)

    assert results[0].created is True
    text = results[0].path.read_text(encoding="utf-8")
    assert "> 状态: proposed" in text
    assert "> Dedupe: same" in text
    assert "## 指标来源" in text


def test_write_candidate_backlogs_reuses_existing_dedupe(tmp_path: Path) -> None:
    candidate = candidates_from_signals([_signal("a", "same")])[0]
    write_candidate_backlogs([candidate], out_dir=tmp_path)

    second = write_candidate_backlogs([candidate], out_dir=tmp_path)

    assert second[0].created is False
    assert second[0].reason == "existing_dedupe"


def test_candidate_markdown_renders_scheduling_fields():
    # B13 (doc 92 §2): scheduling fields make the candidate ingestable
    # into a TaskContract without re-triage.
    from zf.autoresearch.bug_candidates import (
        BugCandidate,
        render_candidate_markdown,
    )

    candidate = BugCandidate(
        bug_id="ZF-AR-BUG-TEST0001",
        dedupe_key="trace-x:stage-y",
        title="quarantine minted candidate",
        summary="rework cap exhausted",
        severity="high",
        source_kind="quarantine",
        allowed_paths=["packages/gateway/**"],
        root_owner_class="slice",
        affinity_tag="gateway",
        lane_hint="lane3",
    )
    text = render_candidate_markdown(candidate)
    assert "> Source-kind: quarantine / Severity: high" in text
    assert "## Scheduling (doc 92 ingest)" in text
    assert "packages/gateway/**" in text
    assert "root_owner_class: slice" in text
    assert "affinity_tag: gateway / lane_hint: lane3" in text


def test_candidate_scheduling_fields_default_safe():
    from zf.autoresearch.bug_candidates import (
        BugCandidate,
        render_candidate_markdown,
    )

    candidate = BugCandidate(bug_id="B", dedupe_key="k", title="t")
    text = render_candidate_markdown(candidate)
    assert "> Source-kind: runtime" in text
    assert "root_owner_class: none" in text
    assert "<shepherd/operator fills>" in text


def test_quarantine_candidate_minting_and_dedupe(tmp_path):
    # B12 (doc 92 §5): cap 耗尽铸候选;同 trace+stage 不重复铸。
    from zf.autoresearch.bug_candidates import write_candidate_backlogs
    from zf.runtime.candidate_rework import (
        ReworkPlan,
        quarantine_candidate_from_plan,
    )

    plan = ReworkPlan(
        action="escalate",
        pdd_id="CJMIN-PI-NODE-V2-001",
        trace_id="trace-r25",
        target_ref="",
        attempt=3,
        source_event_id="evt-reject-3",
        source_event_type="review.rejected",
        feedback=("web-tui pty bridge failing", "gateway fallback drift"),
        classification="",
    )
    candidate = quarantine_candidate_from_plan(plan)
    assert candidate.source_kind == "quarantine"
    assert candidate.dedupe_key == "quarantine:trace-r25:review.rejected"
    assert "2 attempts" in candidate.title
    assert "pty bridge" in candidate.summary

    out = tmp_path / "backlogs"
    first = write_candidate_backlogs([candidate], out_dir=out)
    assert first[0].created is True
    text = first[0].path.read_text(encoding="utf-8")
    assert "> Source-kind: quarantine / Severity: high" in text

    again = write_candidate_backlogs([candidate], out_dir=out)
    assert again[0].created is False
    assert again[0].reason == "existing_dedupe"
