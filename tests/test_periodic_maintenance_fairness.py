"""Tool activity is not permission to starve the periodic recovery owner."""

import pytest

from tests.test_writer_fanout_runtime import _state
from zf.cli.start import _run_orchestrator_idle_tick
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def _hook(log):
    return EventWriter(log).append(ZfEvent(
        type="codex.hook.post_tool_use", actor="dev-1", payload={"tool_name": "Read"}))


def test_periodic_entry_cannot_defer_recovery_forever_under_tool_traffic(tmp_path, monkeypatch):
    from zf.runtime import orchestrator_periodic_sweep

    _, log, _, runtime = _state(tmp_path)
    runtime.session_store.create(project_root=str(tmp_path))
    maintained = []
    handled = []
    monkeypatch.setattr(orchestrator_periodic_sweep, "run_replay_sweep",
        lambda runtime, *, events: maintained.append(events))
    original = runtime._apply_housekeeping
    def observe(event):
        handled.append(event.id)
        original(event)
    monkeypatch.setattr(runtime, "_apply_housekeeping", observe)
    for index in range(6):
        event = _hook(log)
        boundary = log.current_offset()
        _run_orchestrator_idle_tick(runtime)
        assert handled.count(event.id) == 1
        assert runtime._load_offset() >= boundary
        assert len(maintained) >= (index + 1) // 2
    assert maintained and all(event is None for event in maintained)


def test_forced_maintenance_keeps_callback_appends_for_durable_catchup(tmp_path, monkeypatch):
    from zf.runtime import orchestrator_periodic_sweep

    _, log, _, runtime = _state(tmp_path)
    runtime.session_store.create(project_root=str(tmp_path))
    _hook(log)
    _run_orchestrator_idle_tick(runtime)
    hook = _hook(log)
    original = runtime._apply_housekeeping
    appended = []
    handled = []
    def observe(event):
        handled.append(event.id)
        original(event)
        if event.id == hook.id:
            appended.append(EventWriter(log).append(ZfEvent(type="test.followup", actor="runtime")))
    monkeypatch.setattr(runtime, "_apply_housekeeping", observe)
    maintained = []
    monkeypatch.setattr(orchestrator_periodic_sweep, "run_replay_sweep",
        lambda runtime, *, events: maintained.append(events))
    _run_orchestrator_idle_tick(runtime)
    assert maintained == [None]
    assert handled.count(hook.id) == 1
    assert appended and handled.count(appended[0].id) == 1
    assert not log.read_from_offset(runtime._load_offset())[0]


@pytest.mark.parametrize("control", ["user.message", "codex.hook.pre_tool_use"])
def test_control_event_uses_normal_cycle_after_light_catchup(tmp_path, monkeypatch, control):
    from zf.runtime import orchestrator_periodic_sweep

    _, log, _, runtime = _state(tmp_path)
    runtime.session_store.create(project_root=str(tmp_path))
    _hook(log)
    _run_orchestrator_idle_tick(runtime)
    EventWriter(log).append(ZfEvent(type=control, actor="owner",
        payload={"text": "continue", "permissionDecision": "deny"}))
    maintained = []
    monkeypatch.setattr(orchestrator_periodic_sweep, "run_replay_sweep",
        lambda runtime, *, events: maintained.append(events))
    _run_orchestrator_idle_tick(runtime)
    assert maintained == [None]
