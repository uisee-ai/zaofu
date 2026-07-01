"""run-chain.v1 — dag_run-style end-to-end stage chain (S-D)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.run_chain import build_run_chain

T0 = datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc)
STAGES = ["refactor.scan.requested", "zaofu.refactor.plan.ready", "task_map.ready", "judge.passed"]


def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _events() -> list[ZfEvent]:
    return [
        ZfEvent(type="refactor.scan.requested", actor="zf-cli", ts=_ts(0)),
        ZfEvent(type="zaofu.refactor.plan.ready", actor="arch", ts=_ts(20), causation_id="evt-root"),
        ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T1", ts=_ts(30)),
        ZfEvent(type="task_map.ready", actor="zf-cli", ts=_ts(40)),
    ]


def test_chain_done_active_waiting():
    chain = build_run_chain(_events(), stage_order=STAGES)
    statuses = {s["stage"]: s["status"] for s in chain["stages"]}
    assert statuses["refactor.scan.requested"] == "done"
    assert statuses["zaofu.refactor.plan.ready"] == "done"
    assert statuses["task_map.ready"] == "done"
    assert statuses["judge.passed"] == "active"  # next unreached stage
    assert chain["status"] == "in_progress"
    assert chain["trigger"]["type"] == "refactor.scan.requested"


def test_chain_carries_causation_and_window_tasks():
    chain = build_run_chain(_events(), stage_order=STAGES)
    plan = next(s for s in chain["stages"] if s["stage"] == "zaofu.refactor.plan.ready")
    assert plan["causation_id"] == "evt-root"
    task_map = next(s for s in chain["stages"] if s["stage"] == "task_map.ready")
    assert task_map["task_ids"] == ["T1"]  # dispatched within plan->task_map window


def test_no_stage_order_degrades_explicitly():
    chain = build_run_chain(_events(), stage_order=[])
    assert chain["status"] == "no_stage_order"
    assert chain["stages"] == []


def test_accepts_event_slice_tuples():
    chain = build_run_chain(list(enumerate(_events())), stage_order=STAGES)
    assert chain["status"] == "in_progress"


def test_active_stage_lists_open_window_tasks():
    events = _events() + [
        ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T9", ts=_ts(50)),
    ]
    chain = build_run_chain(events, stage_order=STAGES)
    judge = next(s for s in chain["stages"] if s["stage"] == "judge.passed")
    assert judge["status"] == "active"
    assert judge["task_ids"] == ["T9"]


def test_stage_seq_range_from_event_slice():
    chain = build_run_chain(list(enumerate(_events())), stage_order=STAGES)
    scan = next(s for s in chain["stages"] if s["stage"] == "refactor.scan.requested")
    assert scan["seq_first"] == 0 and scan["seq_last"] == 0
    plan = next(s for s in chain["stages"] if s["stage"] == "zaofu.refactor.plan.ready")
    assert plan["seq_first"] == 1
