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
