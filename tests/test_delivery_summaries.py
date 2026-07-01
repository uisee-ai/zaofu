"""doc 82 P3 — trace-level score / deposition / observability-ref summaries."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.delivery_summaries import (
    build_deposition_summary,
    build_observability_refs,
    build_score_summary,
)


def _cycle(cycle_id: str, **kw) -> dict:
    base = {"cycle_id": cycle_id, "status": "completed", "deposition": "proposal_only",
            "score_delta": None, "baseline_score": None, "candidate_score": None}
    base.update(kw)
    return base


# --- score_summary ---

def test_score_summary_empty_cycles():
    out = build_score_summary([])
    assert out["schema_version"] == "delivery-score-summary.v1"
    assert out["scored_cycle_count"] == 0
    assert out["latest"] == {} and out["best"] == {}


def test_score_summary_ignores_unscored_cycles():
    out = build_score_summary([_cycle("a"), _cycle("b")])
    assert out["scored_cycle_count"] == 0


def test_score_summary_latest_and_best_differ():
    cycles = [
        _cycle("a", score_delta=14.2, baseline_score=61.4, candidate_score=75.6),
        _cycle("b", score_delta=3.1, baseline_score=70.0, candidate_score=73.1),
    ]
    out = build_score_summary(cycles)
    assert out["scored_cycle_count"] == 2
    assert out["latest"]["cycle_id"] == "b"
    assert out["latest"]["score_delta"] == 3.1
    assert out["best"]["cycle_id"] == "a"
    assert out["best"]["score_delta"] == 14.2


def test_score_summary_single_candidate_without_delta():
    # bug-fix candidate without baseline run: candidate_score only, no delta
    out = build_score_summary([_cycle("a", candidate_score=75.6)])
    assert out["scored_cycle_count"] == 1
    assert out["latest"]["candidate_score"] == 75.6
    assert out["best"] == {}  # no delta -> no best


# --- deposition_summary ---

def test_deposition_summary_counts_and_latest():
    cycles = [
        _cycle("a", deposition="proposal_only"),
        _cycle("b", deposition="proposal_only"),
        _cycle("c", deposition="adopted"),
    ]
    out = build_deposition_summary(cycles, None)
    assert out["counts"] == {"proposal_only": 2, "adopted": 1}
    assert out["latest_deposition"] == "adopted"
    assert out["replan_gate_status"] == "none"
    assert out["owner_decision_required"] is False


def test_deposition_summary_blocked_gate_requires_owner():
    gate = {"status": "blocked", "latest_eval": {"decision": "reject"}}
    out = build_deposition_summary([], gate)
    assert out["replan_gate_status"] == "blocked"
    assert out["owner_decision_required"] is True


def test_deposition_summary_owner_decision_eval():
    gate = {"status": "evaluated", "latest_eval": {"decision": "owner_decision"}}
    out = build_deposition_summary([], gate)
    assert out["owner_decision_required"] is True


def test_deposition_summary_adopted_gate_no_owner_needed():
    gate = {"status": "adopted", "latest_eval": {"decision": "adopt"}}
    out = build_deposition_summary([], gate)
    assert out["owner_decision_required"] is False


# --- observability_refs ---

def _ev(event_id: str, etype: str, correlation_id: str | None) -> ZfEvent:
    return ZfEvent(type=etype, id=event_id, correlation_id=correlation_id)


def test_observability_refs_dedupe_by_trace():
    events = [
        (1, _ev("e1", "task.dispatched", "tr-a")),
        (2, _ev("e2", "verify.failed", "tr-a")),
        (3, _ev("e3", "task.dispatched", "tr-b")),
    ]
    refs = build_observability_refs(events)
    assert len(refs) == 2
    by_id = {r["trace_id"]: r for r in refs}
    assert by_id["tr-a"]["event_count"] == 2
    assert by_id["tr-a"]["last_event_id"] == "e2"
    assert by_id["tr-a"]["last_event_type"] == "verify.failed"


def test_observability_refs_primary_trace_first_and_capped():
    events = [(i, _ev(f"e{i}", "x.y", f"tr-{i}")) for i in range(30)]
    events.append((99, _ev("e99", "x.y", "tr-main")))
    refs = build_observability_refs(events, trace_id="tr-main", limit=5)
    assert len(refs) == 5
    assert refs[0]["trace_id"] == "tr-main"


def test_observability_refs_skips_uncorrelated_without_fallback():
    events = [(1, _ev("e1", "x.y", None))]
    assert build_observability_refs(events) == []
    # with feature trace_id fallback the uncorrelated event folds into it
    refs = build_observability_refs(events, trace_id="tr-f")
    assert refs[0]["trace_id"] == "tr-f"
