"""Execution age and worker silence are independent recovery signals."""
from datetime import datetime, timezone
import time

import pytest

from tests.test_fanout_hardening import _state, _start
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


@pytest.mark.parametrize("signal", ["agent.tool.use", "agent.usage", "worker.heartbeat"])
def test_unlimited_child_with_recent_activity_survives_old_deadline(tmp_path, signal):
    _, log, _, runtime = _state(tmp_path, timeout_seconds=0, idle_threshold=600)
    _start(runtime)
    now = time.time() + 8000
    EventWriter(log).append(ZfEvent(
        type=signal, actor="review-a", origin="kernel" if signal == "agent.usage" else "worker",
        ts=datetime.fromtimestamp(now - 10, timezone.utc).isoformat(),
    ))
    runtime._now = lambda: now
    runtime.run_once(events=[])
    assert not any(e.type in {"fanout.child.failed", "fanout.timed_out"}
                   for e in log.read_all())


@pytest.mark.parametrize("signal", [None, "worker.state.changed", "worker.probe.silent"])
def test_unlimited_child_still_detects_silence_despite_kernel_noise(tmp_path, signal):
    _, log, _, runtime = _state(tmp_path, timeout_seconds=0, idle_threshold=600)
    _start(runtime)
    now = time.time() + 700
    if signal:
        EventWriter(log).append(ZfEvent(
            type=signal, actor="review-a", origin="kernel",
            ts=datetime.fromtimestamp(now - 10, timezone.utc).isoformat(),
        ))
    runtime._now = lambda: now
    runtime.run_once(events=[])
    failed = [e for e in log.read_all() if e.type == "fanout.child.failed"]
    assert len(failed) == 1
    assert failed[0].payload["reason"] == "idle"
    assert any(e.type == "review.rejected" for e in log.read_all())


def test_unlimited_queued_wait_does_not_expire_immediately(tmp_path):
    from zf.runtime.fanout_timeout_policy import close_expired_queued_wait
    _, log, _, _ = _state(tmp_path)
    assert not close_expired_queued_wait(
        EventWriter(log), manifest={"children": [{"child_id": "a", "status": "queued"}]},
        fanout_epoch=1, now=8000, timeout_seconds=0,
    )
    assert not log.read_all()


@pytest.mark.parametrize("active", [True, False])
def test_unlimited_synth_uses_activity_instead_of_execution_age(tmp_path, active):
    from tests.test_fanout_hardening import _synth_budget_config, _RecordingTransport, _drive_children_terminal
    from zf.core.events.log import EventLog
    from zf.runtime.orchestrator import Orchestrator

    state = tmp_path / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    runtime = Orchestrator(state, _synth_budget_config(timeout_seconds=0), _RecordingTransport())
    _start(runtime)
    _drive_children_terminal(runtime, log, b_status="completed")
    assert any(e.type == "fanout.synth.dispatched" for e in log.read_all())
    now = time.time() + 8000
    if active:
        EventWriter(log).append(ZfEvent(
            type="agent.tool.use", actor="review-synth",
            ts=datetime.fromtimestamp(now - 10, timezone.utc).isoformat(),
        ))
    runtime._now = lambda: now
    runtime.run_once(events=[])
    terminal = [e for e in log.read_all() if e.type == "fanout.synth.completed"]
    if active:
        assert not terminal
    else:
        assert terminal[-1].payload["liveness_reason"] == "idle"
        assert any(e.type == "review.rejected" for e in log.read_all())
