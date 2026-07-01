"""EVAL-KANBAN-HEALTH-001 — main audit entry tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.kanban_health import (
    _build_coordinator,
    _build_failure_taxonomy,
    _build_recommendations,
    _build_throughput,
    _parse_since,
    build_health_snapshot,
    render_health_md,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class _Ev:
    def __init__(self, etype, ts="", task_id="", payload=None, actor=""):
        self.type = etype
        self.ts = ts
        self.task_id = task_id
        self.payload = payload or {}
        self.actor = actor


class _Task:
    def __init__(self, tid, status, contract=None):
        self.id = tid
        self.status = status
        self.contract = contract


class _Contract:
    def __init__(self, verification_tiers=None):
        self.verification_tiers = verification_tiers or []


# ---------------------------------------------------------------------------
# _parse_since
# ---------------------------------------------------------------------------


def test_parse_since_basic_formats() -> None:
    assert _parse_since(None) is None
    assert _parse_since("") is None
    assert _parse_since("24h") is not None
    assert _parse_since("7d") is not None
    assert _parse_since("30m") is not None
    assert _parse_since("bad") is None


# ---------------------------------------------------------------------------
# _build_throughput
# ---------------------------------------------------------------------------


def test_throughput_counts_completed_and_failed() -> None:
    tasks = [
        _Task("T1", "done"),
        _Task("T2", "done"),
        _Task("T3", "failed"),
        _Task("T4", "in_progress"),
    ]
    out = _build_throughput(tasks, [])
    assert out["tasks_completed"] == 2
    assert out["tasks_failed"] == 1


def test_throughput_rework_looped_threshold() -> None:
    tasks = [_Task("T1", "in_progress")]
    events = [
        _Ev("review.rejected", task_id="T1"),
        _Ev("review.rejected", task_id="T1"),
        _Ev("test.failed", task_id="T1"),
    ]
    out = _build_throughput(tasks, events, rework_threshold=3)
    assert out["rework_looped_count"] == 1
    assert "T1" in out["rework_looped_tasks"]


def test_throughput_rework_below_threshold_not_counted() -> None:
    tasks = [_Task("T1", "in_progress")]
    events = [
        _Ev("review.rejected", task_id="T1"),
        _Ev("test.failed", task_id="T1"),
    ]
    out = _build_throughput(tasks, events, rework_threshold=3)
    assert out["rework_looped_count"] == 0


# ---------------------------------------------------------------------------
# _build_failure_taxonomy
# ---------------------------------------------------------------------------


def test_failure_taxonomy_groups_by_bucket() -> None:
    events = [
        _Ev("task.rework.triage.completed", payload={
            "taxonomy_bucket": "infra", "classification": "worker_stuck",
        }),
        _Ev("task.rework.triage.completed", payload={
            "taxonomy_bucket": "infra", "classification": "transport_failed",
        }),
        _Ev("task.rework.triage.completed", payload={
            "taxonomy_bucket": "content", "classification": "product_issue",
        }),
        _Ev("dev.build.done"),  # not a rework triage
    ]
    out = _build_failure_taxonomy(events)
    assert out["by_bucket"]["infra"] == 2
    assert out["by_bucket"]["content"] == 1
    assert out["total"] == 3


def test_failure_taxonomy_empty_returns_zero() -> None:
    out = _build_failure_taxonomy([])
    assert out["total"] == 0
    assert out["by_bucket"] == {}


# ---------------------------------------------------------------------------
# _build_coordinator
# ---------------------------------------------------------------------------


def test_coordinator_no_events_friendly() -> None:
    out = _build_coordinator([])
    assert out["total_wakes"] == 0
    assert out["health_band"] == "n/a"


def test_coordinator_healthy_ratio() -> None:
    events = [
        _Ev("orchestrator.decision.recorded", payload={"decision": "dispatch"})
        for _ in range(10)
    ] + [
        _Ev("orchestrator.decision.recorded", payload={"decision": "no_action"})
        for _ in range(10)
    ]
    out = _build_coordinator(events)
    assert out["health_band"] == "healthy"
    assert out["dispatch_no_action_ratio"] == 1.0


def test_coordinator_over_cautious_ratio() -> None:
    events = (
        [_Ev("orchestrator.decision.recorded", payload={"decision": "dispatch"}) for _ in range(1)]
        + [_Ev("orchestrator.decision.recorded", payload={"decision": "no_action"}) for _ in range(10)]
    )
    out = _build_coordinator(events)
    assert out["health_band"] == "over_cautious"


def test_coordinator_outcome_reason_grouping() -> None:
    events = [
        _Ev("orchestrator.decision.recorded", payload={
            "decision": "no_action", "outcome_reason": "idle_sweep",
        }),
        _Ev("orchestrator.decision.recorded", payload={
            "decision": "no_action", "outcome_reason": "out_of_scope",
        }),
        _Ev("orchestrator.decision.recorded", payload={
            "decision": "blocked", "outcome_reason": "circuit_open",
        }),
    ]
    out = _build_coordinator(events)
    assert out["by_outcome_reason"]["no_action"] == {
        "idle_sweep": 1, "out_of_scope": 1,
    }
    assert out["by_outcome_reason"]["blocked"] == {"circuit_open": 1}


# ---------------------------------------------------------------------------
# _build_recommendations
# ---------------------------------------------------------------------------


def test_recommendations_flags_missing_acceptance() -> None:
    snap = {
        "workflow_coverage": {
            "tasks_missing_acceptance_criteria": ["T-X"],
        },
        "failure_taxonomy": {"by_bucket": {}},
        "coordinator": {"health_band": "healthy"},
        "role_health": {},
        "metric_diagnostics": [],
    }
    recs = _build_recommendations(snap)
    assert any("T-X" in r and "acceptance_criteria" in r for r in recs)


def test_recommendations_flags_content_failures() -> None:
    snap = {
        "workflow_coverage": {"tasks_missing_acceptance_criteria": []},
        "failure_taxonomy": {"by_bucket": {"content": 6, "infra": 1}},
        "coordinator": {"health_band": "healthy"},
        "role_health": {},
        "metric_diagnostics": [],
    }
    recs = _build_recommendations(snap)
    assert any("content failures" in r for r in recs)


def test_recommendations_flags_over_cautious_coordinator() -> None:
    snap = {
        "workflow_coverage": {"tasks_missing_acceptance_criteria": []},
        "failure_taxonomy": {"by_bucket": {}},
        "coordinator": {"health_band": "over_cautious"},
        "role_health": {},
        "metric_diagnostics": [],
    }
    recs = _build_recommendations(snap)
    assert any("over-cautious" in r for r in recs)


def test_recommendations_flags_idle_role() -> None:
    snap = {
        "workflow_coverage": {"tasks_missing_acceptance_criteria": []},
        "failure_taxonomy": {"by_bucket": {}},
        "coordinator": {"health_band": "healthy"},
        "role_health": {
            "review": {"warning": True, "idle_seconds": 100000,
                       "completion_count": 5},
        },
        "metric_diagnostics": [],
    }
    recs = _build_recommendations(snap)
    assert any("review" in r and "scaling" in r for r in recs)


def test_recommendations_flags_zero_completion_role() -> None:
    snap = {
        "workflow_coverage": {"tasks_missing_acceptance_criteria": []},
        "failure_taxonomy": {"by_bucket": {}},
        "coordinator": {"health_band": "healthy"},
        "role_health": {
            "arch": {"warning": True, "idle_seconds": None,
                     "completion_count": 0},
        },
        "metric_diagnostics": [],
    }
    recs = _build_recommendations(snap)
    assert any("arch" in r and "0 completions" in r for r in recs)


def test_recommendations_flags_critical_metrics() -> None:
    snap = {
        "workflow_coverage": {"tasks_missing_acceptance_criteria": []},
        "failure_taxonomy": {"by_bucket": {}},
        "coordinator": {"health_band": "healthy"},
        "role_health": {},
        "metric_diagnostics": [
            {"metric_name": "mtts", "health_band": "critical"},
            {"metric_name": "vcr", "health_band": "critical"},
        ],
    }
    recs = _build_recommendations(snap)
    assert any("Metrics critical" in r for r in recs)


def test_recommendations_empty_when_all_healthy() -> None:
    snap = {
        "workflow_coverage": {"tasks_missing_acceptance_criteria": []},
        "failure_taxonomy": {"by_bucket": {"infra": 5}},
        "coordinator": {"health_band": "healthy"},
        "role_health": {},
        "metric_diagnostics": [],
    }
    recs = _build_recommendations(snap)
    assert recs == []


# ---------------------------------------------------------------------------
# render_health_md
# ---------------------------------------------------------------------------


def test_render_md_includes_all_6_sections() -> None:
    snap = {
        "window": "7d",
        "events_considered": 0,
        "tasks_total": 0,
        "throughput": {
            "tasks_completed": 0, "tasks_failed": 0,
            "rework_looped_count": 0, "rework_looped_tasks": [],
            "rework_threshold": 3,
        },
        "workflow_coverage": {
            "audited": 0, "complete": 0, "partial": 0,
            "completeness_ratio": 1.0,
            "tasks_missing_acceptance_criteria": [],
            "stage_order_violations": [],
        },
        "role_health": {},
        "failure_taxonomy": {"by_bucket": {}, "by_classification": {}, "total": 0},
        "coordinator": {
            "total_wakes": 0, "counts": {},
            "dispatch_no_action_ratio": None,
            "health_band": "n/a", "by_outcome_reason": {},
        },
        "metrics_snapshot": {},
        "metric_diagnostics": [],
        "recommendations": [],
    }
    md = render_health_md(snap)
    assert "Kanban Health · 7d window" in md
    assert "THROUGHPUT" in md
    assert "WORKFLOW COVERAGE" in md
    assert "ROLE HEALTH" in md
    assert "FAILURE TAXONOMY" in md
    assert "COORDINATOR" in md
    assert "METRICS SNAPSHOT" in md
    assert "RECOMMENDATIONS" in md


def test_render_md_no_actions_when_healthy() -> None:
    snap = {
        "window": "all", "events_considered": 0, "tasks_total": 0,
        "throughput": {
            "tasks_completed": 5, "tasks_failed": 0,
            "rework_looped_count": 0, "rework_looped_tasks": [],
            "rework_threshold": 3,
        },
        "workflow_coverage": {
            "audited": 5, "complete": 5, "partial": 0,
            "completeness_ratio": 1.0,
            "tasks_missing_acceptance_criteria": [],
            "stage_order_violations": [],
        },
        "role_health": {},
        "failure_taxonomy": {"by_bucket": {}, "by_classification": {}, "total": 0},
        "coordinator": {
            "total_wakes": 5, "counts": {"dispatch": 5},
            "dispatch_no_action_ratio": None, "health_band": "n/a",
            "by_outcome_reason": {},
        },
        "metrics_snapshot": {},
        "metric_diagnostics": [],
        "recommendations": [],
    }
    md = render_health_md(snap)
    assert "no actions needed" in md


# ---------------------------------------------------------------------------
# build_health_snapshot end-to-end (with a synthetic state_dir)
# ---------------------------------------------------------------------------


def test_build_health_snapshot_empty_state(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("")
    (state_dir / "kanban.json").write_text("[]")

    class _Cfg:
        roles = []

    snap = build_health_snapshot(
        state_dir=state_dir, config=_Cfg(), since=None,
    )
    assert snap["window"] == "all"
    assert snap["throughput"]["tasks_completed"] == 0
    assert snap["coordinator"]["total_wakes"] == 0
    assert isinstance(snap["metric_diagnostics"], list)


def test_build_health_snapshot_with_window(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("")
    (state_dir / "kanban.json").write_text("[]")

    class _Cfg:
        roles = []

    snap = build_health_snapshot(
        state_dir=state_dir, config=_Cfg(), since="7d",
    )
    assert snap["window"] == "7d"
