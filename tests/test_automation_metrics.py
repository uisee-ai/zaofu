"""Tests for automation_metrics: percentile + cross-cutting scorecard.

P0 of tasks/2026-06-15-1422 — verifies the management scorecard counts only
what the kernel emits (no re-judgement) and that percentile replaces the mean.
"""
from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.automation_metrics import (
    build_archetype_matrix,
    commit_counts_by_task,
    cross_cutting_scorecard,
    percentile,
)


def _task(tid, status, feature_id):
    return SimpleNamespace(
        id=tid, status=status, contract=SimpleNamespace(feature_id=feature_id),
    )


def test_percentile_empty_and_single():
    assert percentile([], 50) is None
    assert percentile([None, None], 90) is None
    assert percentile([5], 90) == 5.0


def test_percentile_interpolation():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    # long tail: p90 close to the worst-case, not dragged down by the mean
    p90 = percentile([2, 4, 6, 100], 90)
    assert p90 is not None and p90 > 70
    assert percentile([2, 4, 6, 100], 50) == 5.0


def test_percentile_drops_none():
    assert percentile([2, None, 4, None, 6], 50) == 4.0


def test_scorecard_buckets_by_event_type():
    events = [
        ZfEvent(type="runtime.safe_halted", actor="kernel"),
        ZfEvent(type="worker.stuck", actor="dev-1"),
        ZfEvent(type="workflow.inline_override", actor="operator"),
        ZfEvent(type="workflow.inline_override", actor="operator"),
        ZfEvent(type="human.escalate", actor="dev-1"),
        ZfEvent(type="task.created", actor="zf-cli"),  # unrelated, ignored
    ]
    card = cross_cutting_scorecard(events)

    assert card["reliability"]["safe_halt"] == 1
    assert card["reliability"]["worker_stuck"] == 1
    assert card["reliability"]["incidents_total"] == 2
    assert card["reliability"]["critical_total"] == 1  # safe_halt is critical

    assert card["governance"]["inline_override"] == 2
    assert card["governance"]["violations_total"] == 2

    assert card["autonomy"]["escalations"] == 1
    assert card["autonomy"]["interventions_total"] == 1


def test_scorecard_empty_is_all_zero():
    card = cross_cutting_scorecard([])
    assert card["reliability"]["incidents_total"] == 0
    assert card["governance"]["violations_total"] == 0
    assert card["autonomy"]["interventions_total"] == 0


def test_archetype_matrix_buckets_by_feature_classification():
    events = [
        ZfEvent(type="task.created", payload={"feature_id": "F1"}),
        ZfEvent(type="refactor.scan.started", payload={"feature_id": "F2"}),
        ZfEvent(type="zaofu.bug.detected", payload={"feature_id": "F3"}),
    ]
    tasks = [
        _task("T1", "done", "F1"),  # feature, no rework
        _task("T2", "done", "F1"),  # feature, reworked
        _task("T3", "done", "F2"),  # refactor
        _task("T4", "done", "F3"),  # bugfix
        _task("T5", "in_progress", "F1"),  # not done — excluded from done count
    ]
    durations = {"T1": 2.0, "T2": 4.0, "T3": 6.0, "T4": 3.0}
    matrix = build_archetype_matrix(
        tasks,
        events,
        duration_hours=lambda t: durations.get(t.id),
        rework_task_ids={"T2"},
    )

    assert matrix["feature"]["features"] == 1
    assert matrix["feature"]["done_tasks"] == 2
    assert matrix["feature"]["first_pass_yield"] == 0.5  # 1 of 2 done w/o rework
    assert matrix["refactor"]["done_tasks"] == 1
    assert matrix["bugfix"]["done_tasks"] == 1
    assert matrix["refactor"]["cycle_p50_hours"] == 6.0


def test_archetype_matrix_empty_inputs():
    matrix = build_archetype_matrix(
        [], [], duration_hours=lambda t: None, rework_task_ids=set(),
    )
    for archetype in ("feature", "refactor", "bugfix"):
        assert matrix[archetype]["done_tasks"] == 0
        assert matrix[archetype]["first_pass_yield"] is None
        assert matrix[archetype]["commits"] == 0
        assert matrix[archetype]["commits_per_feature"] is None


def test_commit_counts_by_task_latest_wins():
    events = [
        ZfEvent(
            type="candidate.task_ref.applied", task_id="T1",
            payload={"selected_commit_count": 3},
        ),
        ZfEvent(
            type="candidate.task_ref.applied", task_id="T2",
            payload={"task_commits": ["a", "b"]},  # falls back to len
        ),
        ZfEvent(
            type="candidate.task_ref.applied", task_id="T1",
            payload={"selected_commit_count": 5},  # latest wins
        ),
        ZfEvent(type="task.created", task_id="T3"),  # ignored
    ]
    counts = commit_counts_by_task(events)
    assert counts == {"T1": 5, "T2": 2}


def test_archetype_matrix_commit_rollup():
    events = [ZfEvent(type="task.created", payload={"feature_id": "F1"})]
    tasks = [_task("T1", "done", "F1"), _task("T2", "done", "F1")]
    matrix = build_archetype_matrix(
        tasks,
        events,
        duration_hours=lambda t: 2.0,
        rework_task_ids=set(),
        commit_counts={"T1": 4, "T2": 8},
    )
    assert matrix["feature"]["commits"] == 12
    assert matrix["feature"]["commits_per_feature"] == 12.0  # 12 commits / 1 feature
