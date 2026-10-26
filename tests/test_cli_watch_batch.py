"""The CLI handles one immutable watcher snapshot in one scheduling cycle."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.cli.watch_batch import make_watch_callbacks
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.watcher import EventWatcher


def test_wake_burst_runs_once_in_ledger_order_and_preserves_append_boundary(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    calls = []
    appended = ZfEvent(type="verify.child.completed")

    def run_once(*, events, consumed_offset=None):
        calls.append((list(events), consumed_offset))
        if len(calls) == 1:
            log.append(appended)

    on_event, on_batch = make_watch_callbacks(
        orchestrator=SimpleNamespace(run_once=run_once),
        wake_patterns={"worker.state.changed", "verify.child.completed"},
        wake_worthy_fn=lambda _: True,
        rate_limiter=SimpleNamespace(allow=lambda _: True),
        simulation=False, event_log=log, stop_watcher=lambda: None,
    )
    watcher = EventWatcher(log.path, on_event, on_batch_consumed=on_batch, event_log=log)
    events = [ZfEvent(type="agent.usage"), ZfEvent(type="worker.state.changed"),
              ZfEvent(type="worker.state.changed"), ZfEvent(type="verify.child.completed")]
    for event in events:
        log.append(event)
    boundary = log.path.stat().st_size
    watcher.poll_once()
    assert calls == [(events, boundary)]
    watcher.poll_once()
    assert calls[1] == ([appended], log.path.stat().st_size)


def test_pure_observations_and_denied_wakes_remain_for_durable_tick(tmp_path: Path):
    calls = []
    checked = []
    log = EventLog(tmp_path / "events.jsonl")
    _, on_batch = make_watch_callbacks(
        orchestrator=SimpleNamespace(run_once=lambda **kw: calls.append(kw)),
        wake_patterns={"worker.state.changed", "verify.child.completed"},
        wake_worthy_fn=lambda e: e.type != "worker.state.changed",
        rate_limiter=SimpleNamespace(allow=lambda kind: checked.append(kind) or False),
        simulation=False, event_log=log, stop_watcher=lambda: None,
    )
    on_batch([ZfEvent(type="agent.usage")], 100)
    on_batch([ZfEvent(type="worker.state.changed"), ZfEvent(type="verify.child.completed")], 200)
    assert calls == []
    assert checked == ["verify.child.completed"]


def test_rate_limited_events_are_consumed_when_another_event_wakes(tmp_path: Path):
    calls = []
    checked = []
    log = EventLog(tmp_path / "events.jsonl")

    def allow(kind: str) -> bool:
        checked.append(kind)
        return kind == "verify.child.completed"

    _, on_batch = make_watch_callbacks(
        orchestrator=SimpleNamespace(run_once=lambda **kw: calls.append(kw)),
        wake_patterns={"worker.state.changed", "verify.child.completed"},
        wake_worthy_fn=lambda _: True, rate_limiter=SimpleNamespace(allow=allow),
        simulation=False, event_log=log, stop_watcher=lambda: None,
    )
    events = [ZfEvent(type="verify.child.completed"), ZfEvent(type="worker.state.changed")]
    on_batch(events, 321)
    assert calls == [{"events": events, "consumed_offset": 321}]
    assert checked == [e.type for e in events]


@pytest.mark.parametrize("terminal_type", ["run.goal.completed", "run.goal.blocked"])
def test_simulation_settles_terminal_before_emitting_done_and_stopping(tmp_path: Path, terminal_type: str):
    log = EventLog(tmp_path / "events.jsonl")
    terminal = ZfEvent(type=terminal_type, correlation_id="RUN-SIM", payload={"run_id": "RUN-SIM"})
    log.append(terminal)
    order = []

    def run_once(*, events, consumed_offset):
        assert events == [terminal]
        assert consumed_offset == 654
        order.append("settled")
        log.append(ZfEvent(type="task.status_changed", task_id="TASK-SIM"))

    _, on_batch = make_watch_callbacks(
        orchestrator=SimpleNamespace(run_once=run_once), wake_patterns=set(),
        wake_worthy_fn=lambda _: False,
        rate_limiter=SimpleNamespace(allow=lambda _: False),
        simulation=True, event_log=log, stop_watcher=lambda: order.append("stop"),
    )
    on_batch([terminal], 654)
    assert order == ["settled", "stop"]
    assert [e.type for e in log.read_all()] == [terminal_type, "task.status_changed", "simulation.done"]
    on_batch([terminal], 654)
    assert order.count("stop") == 1
    assert sum(e.type == "simulation.done" for e in log.read_all()) == 1


def test_failed_cycle_does_not_emit_simulation_completion(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    terminal = ZfEvent(type="run.goal.completed", correlation_id="RUN-SIM")
    log.append(terminal)
    stopped = []

    def fail(**_kwargs):
        raise RuntimeError("settlement failed")

    _, on_batch = make_watch_callbacks(
        orchestrator=SimpleNamespace(run_once=fail), wake_patterns=set(),
        wake_worthy_fn=lambda _: False,
        rate_limiter=SimpleNamespace(allow=lambda _: False),
        simulation=True, event_log=log, stop_watcher=lambda: stopped.append(True),
    )
    with pytest.raises(RuntimeError, match="settlement failed"):
        on_batch([terminal], 321)
    assert not stopped
    assert [e.type for e in log.read_all()] == ["run.goal.completed"]
