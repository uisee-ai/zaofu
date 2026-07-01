"""Delivery slice-1 backbone metrics (queue wait / first response / segments /
backedge / convergence / archetype)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.delivery_flow_metrics import (
    build_delivery_flow_metrics,
    derive_workflow_archetype,
)

T0 = datetime(2026, 6, 10, 9, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _timeline() -> list[ZfEvent]:
    return [
        ZfEvent(type="task.created", actor="zf-cli", task_id="T1", ts=_ts(0)),
        ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(9)),
        ZfEvent(type="worker.heartbeat", actor="dev-lane-0", task_id="T1", ts=_ts(9.5)),
        ZfEvent(type="dev.build.done", actor="dev-lane-0", task_id="T1", ts=_ts(40)),
        ZfEvent(type="static_gate.passed", actor="zf-cli", task_id="T1", ts=_ts(41)),
        ZfEvent(type="verify.failed", actor="zf-cli", task_id="T1", ts=_ts(45)),
        ZfEvent(
            type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(50),
            payload={"rework_kind": "workflow_stage_backedge", "attempt": 2},
        ),
        ZfEvent(type="dev.build.done", actor="dev-lane-0", task_id="T1", ts=_ts(70)),
        ZfEvent(type="verify.passed", actor="zf-cli", task_id="T1", ts=_ts(75)),
    ]


def test_segments_first_response_and_queue_wait():
    metrics = build_delivery_flow_metrics(_timeline())["tasks"]["T1"]
    assert metrics["queue_wait_seconds"] == 9 * 60
    assert metrics["first_response_seconds"] == 30  # dispatch 9m -> heartbeat 9.5m
    assert metrics["rework_seconds"] == 25 * 60  # rework dispatch 50m -> last 75m
    # total 75m - wait 9m - rework 25m = 41m active
    assert metrics["active_seconds"] == 41 * 60


def test_backedge_count_and_convergence_rounds():
    metrics = build_delivery_flow_metrics(_timeline())["tasks"]["T1"]
    assert metrics["backedge_count"] == 1
    assert metrics["convergence"] == [
        {"round": 1, "passed": 1, "failed": 1},
        {"round": 2, "passed": 1, "failed": 0},
    ]


def test_task_without_dispatch_has_null_waits():
    events = [ZfEvent(type="task.created", actor="zf-cli", task_id="T9", ts=_ts(0))]
    metrics = build_delivery_flow_metrics(events)["tasks"]["T9"]
    assert metrics["queue_wait_seconds"] is None
    assert metrics["first_response_seconds"] is None
    assert metrics["convergence"] == []


def test_archetype_inference():
    assert derive_workflow_archetype(
        [ZfEvent(type="refactor.scan.requested", actor="zf-cli", ts=_ts(0))]
    ) == "refactor"
    assert derive_workflow_archetype(
        [ZfEvent(type="zaofu.bug.detected", actor="zf-cli", ts=_ts(0))]
    ) == "bugfix"
    assert derive_workflow_archetype(
        [ZfEvent(type="feature.created", actor="zf-cli", ts=_ts(0))]
    ) == "feature"


def test_flow_metrics_rides_delivery_trace_payload():
    from zf.runtime.delivery_trace import build_delivery_trace

    trace = build_delivery_trace(
        feature_id="F1", generated_at=_ts(80),
        events=list(enumerate(_timeline())), tasks={},
    )
    assert trace["workflow_archetype"] == "feature"
    assert "T1" in trace["flow_metrics"]["tasks"]
