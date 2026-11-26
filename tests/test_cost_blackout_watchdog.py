"""P0-8(审计 D9 G1):dispatch 活跃而 usage 停更 → cost.usage.blackout。

cangjie r5 实证:12:30 后 11h、4891 事件、140 次 dispatch,usage 零更新,
预算门按 $102.78 旧值放行,零告警。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.tick_services import (
    TickServiceIntervals,
    TickServiceState,
    _emit_cost_blackout_if_needed,
)


def _ts(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _env(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def test_blackout_fires_on_stale_usage(tmp_path: Path) -> None:
    state_dir, log, writer = _env(tmp_path)
    log.append(ZfEvent(type="agent.usage", ts=_ts(2000), payload={}))
    log.append(ZfEvent(type="task.dispatched", ts=_ts(300), task_id="T-1", payload={}))
    state = TickServiceState()
    fired = _emit_cost_blackout_if_needed(
        event_log=log, event_writer=writer, state_dir=state_dir,
        state=state, intervals=TickServiceIntervals(),
    )
    assert fired is True
    blackout = [e for e in log.read_all() if e.type == "cost.usage.blackout"]
    assert len(blackout) == 1
    # 冷却:立刻再查不重复发
    assert _emit_cost_blackout_if_needed(
        event_log=log, event_writer=writer, state_dir=state_dir,
        state=state, intervals=TickServiceIntervals(),
    ) is False


def test_no_blackout_when_usage_fresh(tmp_path: Path) -> None:
    state_dir, log, writer = _env(tmp_path)
    log.append(ZfEvent(type="task.dispatched", ts=_ts(300), task_id="T-1", payload={}))
    log.append(ZfEvent(type="agent.usage", ts=_ts(60), payload={}))
    assert _emit_cost_blackout_if_needed(
        event_log=log, event_writer=writer, state_dir=state_dir,
        state=TickServiceState(), intervals=TickServiceIntervals(),
    ) is False


def test_startup_grace_ignores_historical_usage_before_new_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr("time.time", lambda: now.timestamp())
    state_dir, log, writer = _env(tmp_path)
    run_id = "run-new-dispatch"
    log.append(ZfEvent(
        type="run.goal.started",
        ts=(now - timedelta(hours=2)).isoformat(),
        correlation_id=run_id,
        payload={"run_id": run_id},
    ))
    log.append(ZfEvent(
        type="agent.usage",
        ts=(now - timedelta(minutes=58)).isoformat(),
        correlation_id=run_id,
        payload={"run_id": run_id},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        ts=(now - timedelta(seconds=7)).isoformat(),
        correlation_id=run_id,
        payload={"run_id": run_id},
    ))

    assert _emit_cost_blackout_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=TickServiceState(),
        intervals=TickServiceIntervals(
            cost_blackout_stale_s=900,
            cost_blackout_startup_grace_s=60,
            cost_blackout_cooldown_s=0,
        ),
    ) is False
    assert not [event for event in log.read_all() if event.type == "cost.usage.blackout"]


def test_startup_grace_expires_at_fixed_clock_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr("time.time", lambda: now.timestamp())
    state_dir, log, writer = _env(tmp_path)
    run_id = "run-grace-boundary"
    log.append(ZfEvent(
        type="agent.usage",
        ts=(now - timedelta(minutes=58)).isoformat(),
        correlation_id=run_id,
        payload={"run_id": run_id},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        ts=(now - timedelta(seconds=60)).isoformat(),
        correlation_id=run_id,
        payload={"run_id": run_id},
    ))

    assert _emit_cost_blackout_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=TickServiceState(),
        intervals=TickServiceIntervals(
            cost_blackout_stale_s=900,
            cost_blackout_startup_grace_s=60,
            cost_blackout_cooldown_s=0,
        ),
    ) is True


def test_no_blackout_without_dispatch_or_idle_run(tmp_path: Path) -> None:
    state_dir, log, writer = _env(tmp_path)
    # 无派发
    log.append(ZfEvent(type="agent.usage", ts=_ts(5000), payload={}))
    assert _emit_cost_blackout_if_needed(
        event_log=log, event_writer=writer, state_dir=state_dir,
        state=TickServiceState(), intervals=TickServiceIntervals(),
    ) is False


def test_ship_completed_closes_cost_blackout_for_its_run(tmp_path: Path) -> None:
    state_dir, log, writer = _env(tmp_path)
    log.append(ZfEvent(
        type="run.goal.started",
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        ts=_ts(300),
        task_id="T-A",
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))
    log.append(ZfEvent(
        type="ship.completed",
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))

    assert _emit_cost_blackout_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=TickServiceState(),
        intervals=TickServiceIntervals(),
    ) is False


def test_cost_blackout_scopes_usage_to_the_active_run(tmp_path: Path) -> None:
    state_dir, log, writer = _env(tmp_path)
    log.append(ZfEvent(
        type="run.goal.started",
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        ts=_ts(300),
        task_id="T-A",
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))
    log.append(ZfEvent(
        type="agent.usage",
        ts=_ts(10),
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))
    log.append(ZfEvent(
        type="ship.completed",
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))
    log.append(ZfEvent(
        type="run.goal.started",
        correlation_id="run-b",
        payload={"run_id": "run-b"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        ts=_ts(300),
        task_id="T-B",
        correlation_id="run-b",
        payload={"run_id": "run-b"},
    ))

    assert _emit_cost_blackout_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=TickServiceState(),
        intervals=TickServiceIntervals(),
    ) is True
    blackout = [
        event for event in log.read_all()
        if event.type == "cost.usage.blackout"
    ][-1]
    assert blackout.payload["run_id"] == "run-b"
    # 派发已冷(run 空闲期)
    log.append(ZfEvent(type="task.dispatched", ts=_ts(7200), task_id="T", payload={}))
    assert _emit_cost_blackout_if_needed(
        event_log=log, event_writer=writer, state_dir=state_dir,
        state=TickServiceState(), intervals=TickServiceIntervals(),
    ) is False


def _settled_task_events():
    run, task, operation = "run-a", "TASK-A", "wop-verify-A"
    common = {"workflow_run_id": run, "operation_id": operation, "request_hash": "a" * 64}
    identity = {"attempt_id": "a-1", "dispatch_id": "d-1"}
    envelope = {"ref": "artifacts/call-results/envelopes/ok.json", "sha256": "b" * 64}
    dispatch = ZfEvent(
        type="task.dispatched",
        origin="kernel",
        task_id=task,
        correlation_id=run,
        ts=_ts(300),
        payload={**common, **identity, "task_id": task},
    )
    admission = ZfEvent(
        type="workflow.call.result.admitted",
        origin="kernel",
        task_id=task,
        correlation_id=run,
        payload={**common, **identity, "admission_status": "admitted", "envelope_ref": envelope},
    )
    settled = ZfEvent(
        type="workflow.operation.settled",
        origin="kernel",
        task_id=task,
        correlation_id=run,
        causation_id=admission.id,
        payload={**common, "task_id": task, "admitted_call_result_ref": envelope},
    )
    return dispatch, admission, settled


def test_cost_watchdog_does_not_treat_a_settled_task_as_active(tmp_path):
    state_dir, log, writer = _env(tmp_path)
    for event in _settled_task_events():
        log.append(event)
    assert not _emit_cost_blackout_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=TickServiceState(),
        intervals=TickServiceIntervals(cost_blackout_stale_s=240, cost_blackout_cooldown_s=0),
    )
    assert not any(e.type == "cost.usage.blackout" for e in log.read_all())


def test_only_exact_kernel_result_settlement_closes_its_dispatch():
    from dataclasses import replace
    from zf.runtime.cost_dispatch_liveness import last_open_cost_dispatch

    dispatch, admitted, settled = _settled_task_events()
    # An unsettled older sibling and a newly opened same-operation attempt keep their own budget monitoring.
    sibling = replace(dispatch, id="sibling", payload={**dispatch.payload, "operation_id": "wop-still-live"})
    assert last_open_cost_dispatch([sibling, dispatch, admitted, settled]) is sibling
    next_attempt = replace(
        dispatch, id="second", payload={**dispatch.payload, "attempt_id": "next", "dispatch_id": "next-dispatch"}
    )
    assert last_open_cost_dispatch([dispatch, next_attempt, admitted, settled]) is next_attempt
    assert last_open_cost_dispatch([dispatch, admitted, settled, next_attempt]) is next_attempt
    assert last_open_cost_dispatch([dispatch, admitted]) is dispatch
    assert last_open_cost_dispatch([dispatch, settled]) is dispatch
    for field, value in (
        ("attempt_id", "unknown"),
        ("dispatch_id", "other"),
        ("request_hash", "other"),
        ("workflow_run_id", "other-run"),
        ("admission_status", "rejected"),
        ("attempt_id", ""),
        ("attempt_id", []),
    ):
        other = replace(admitted, payload={**admitted.payload, field: value})
        assert last_open_cost_dispatch([dispatch, other, settled]) is dispatch
    for other in (
        replace(settled, origin="worker"),
        replace(settled, task_id="other-task"),
        replace(settled, causation_id="missing"),
        replace(
            settled,
            payload={**settled.payload, "admitted_call_result_ref": {"ref": "elsewhere", "sha256": "b" * 64}},
        ),
    ):
        assert last_open_cost_dispatch([dispatch, admitted, other]) is dispatch
    assert last_open_cost_dispatch([dispatch, replace(admitted, origin="worker"), settled]) is dispatch
    assert last_open_cost_dispatch([dispatch, settled, admitted]) is dispatch
    unknown = replace(dispatch, payload={**dispatch.payload, "dispatch_id": []})
    assert last_open_cost_dispatch([unknown, admitted, settled]) is unknown
