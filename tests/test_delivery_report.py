"""Tests for delivery-report.v1 — DAG completion summary (doc 69 S-g)."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.delivery_trace import build_delivery_trace
from zf.runtime.delivery_report import build_delivery_report

_T0 = "2026-05-30T00:00:00+00:00"
_T1 = "2026-05-30T01:30:00+00:00"  # +5400s


def _task(tid, *, status="done", wave=0, phase=""):
    return Task(id=tid, title=tid, status=status,
                contract=TaskContract(feature_id="F-1", wave=wave, phase=phase))


def _task_map():
    return {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "phase": "impl", "wave": 1},
        {"task_id": "T2", "owner_role": "dev", "phase": "impl", "wave": 1},
    ]}


def _report(tasks, events):
    trace = build_delivery_trace(feature_id="F-1", generated_at=_T1, tasks=tasks,
                                 task_map=_task_map(), events=events)
    return build_delivery_report(trace=trace, events=events, generated_at=_T1)


def test_report_shape_and_verdict_shipped():
    tasks = {"T1": _task("T1", wave=1, phase="impl"), "T2": _task("T2", wave=1, phase="impl")}
    events = [
        (1, ZfEvent(type="dev.build.done", id="b1", task_id="T1", ts=_T0)),
        (2, ZfEvent(type="ship.completed", id="s1", task_id="T1", ts=_T1,
                    payload={"final_commit": "abc", "feature_id": "F-1"})),
    ]
    rep = _report(tasks, events)
    assert rep["schema_version"] == "delivery-report.v1"
    assert rep["feature_id"] == "F-1"
    assert rep["trace"]["schema_version"] == "delivery-trace.v1"  # frozen snapshot
    pm = rep["post_mortem"]
    assert pm["verdict"] == "shipped"
    assert pm["ship"]["shipped"] is True and pm["ship"]["merge_ref"] == "abc"
    assert pm["duration_seconds"] == 5400.0
    assert pm["phase_summary"][0]["phase_id"] == "impl"


def test_first_pass_yield_discounts_rework():
    tasks = {"T1": _task("T1", wave=1, phase="impl"), "T2": _task("T2", wave=1, phase="impl")}
    events = [
        (1, ZfEvent(type="task.rework.requested", id="r1", task_id="T1", ts=_T0)),
        (2, ZfEvent(type="dev.build.done", id="b1", task_id="T2", ts=_T1)),
    ]
    rep = _report(tasks, events)
    pm = rep["post_mortem"]
    # 2 done, T1 had rework → first-pass = 1/2
    assert pm["first_pass_yield"] == 0.5
    assert pm["rework_episodes"] == 1


def test_verdict_in_progress_when_not_shipped():
    tasks = {"T1": _task("T1", status="done", wave=1, phase="impl"),
             "T2": _task("T2", status="in_progress", wave=1, phase="impl")}
    rep = _report(tasks, [])
    assert rep["post_mortem"]["verdict"] == "in_progress"
    assert rep["post_mortem"]["ship"]["shipped"] is False


def test_verdict_blocked_on_ship_blocked():
    tasks = {"T1": _task("T1", wave=1, phase="impl"), "T2": _task("T2", wave=1, phase="impl")}
    events = [(1, ZfEvent(type="ship.blocked", id="sb", task_id="T1",
                          payload={"feature_id": "F-1"}))]
    rep = _report(tasks, events)
    assert rep["post_mortem"]["verdict"] == "blocked"
