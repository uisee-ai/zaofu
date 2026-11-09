"""Regress provider turns that go silent while a long reader is active."""

from datetime import datetime, timezone
import time

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from tests.test_fanout_hardening import _fanout_id, _start, _state


def _at(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _event(writer, kind: str, epoch: float, *, actor="review-a", payload=None):
    writer.append(ZfEvent(
        type=kind, actor=actor, ts=_at(epoch), payload=payload or {},
        correlation_id="trace-1",
    ))


@pytest.mark.parametrize(
    ("turn_started", "closed", "silent_seconds", "expected_failed"),
    [
        (True, False, 1000, False),  # Active turn after a long dispatch.
        (True, True, 1000, True),    # Closed turn cannot renew liveness.
        (False, False, 1000, True),  # A turn from before dispatch is stale.
        (True, False, 3601, True),   # A hung turn still reaches a hard bound.
    ],
)
def test_reader_idle_respects_bounded_active_turn(
    tmp_path, turn_started, closed, silent_seconds, expected_failed,
):
    base = time.time()
    _state_dir, log, _transport, orch = _state(
        tmp_path, timeout_seconds=0, max_retries=0, idle_threshold=1,
    )
    orch.config.roles[0].backend = "codex"
    _start(orch)
    fanout_id = _fanout_id(log)
    writer = EventWriter(log)
    _event(writer, "codex.hook.user_prompt_submit", base + (1 if turn_started else -1),
           payload={"session_id": "session", "turn_id": "turn"})
    _event(writer, "agent.usage", base + 4000, payload={"usage": {}})
    if closed:
        _event(writer, "codex.hook.stop", base + 4001,
               payload={"session_id": "session", "turn_id": "turn"})
    orch._now = lambda: base + 4000 + silent_seconds

    orch._check_fanout_timeouts()

    failed = [e for e in log.read_all() if e.type == "fanout.child.failed"
              and e.payload.get("fanout_id") == fanout_id]
    assert bool(failed) is expected_failed


def test_active_turn_does_not_override_stage_timeout(tmp_path):
    base = time.time()
    _, log, _, orch = _state(tmp_path, timeout_seconds=4500, idle_threshold=1)
    orch.config.roles[0].backend = "codex"
    _start(orch)
    writer = EventWriter(log)
    _event(writer, "codex.hook.user_prompt_submit", base + 1,
           payload={"session_id": "session", "turn_id": "turn"})
    _event(writer, "agent.usage", base + 4000, payload={"usage": {}})
    orch._now = lambda: base + 5000

    orch._check_fanout_timeouts()

    failed = [e for e in log.read_all() if e.type == "fanout.child.failed"]
    assert failed and failed[-1].payload["reason"] == "timeout"


def test_synth_active_turn_gets_bounded_grace(tmp_path):
    from tests.test_fanout_hardening import _drive_children_terminal, _RecordingTransport, _synth_budget_config
    from zf.core.events.log import EventLog
    from zf.runtime.orchestrator import Orchestrator

    base = time.time()
    state = tmp_path / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    config = _synth_budget_config(timeout_seconds=0)
    config.roles[2].backend = "codex"
    orch = Orchestrator(state, config, _RecordingTransport())
    _start(orch)
    _drive_children_terminal(orch, log, b_status="completed")
    writer = EventWriter(log)
    _event(writer, "codex.hook.user_prompt_submit", base + 1, actor="review-synth",
           payload={"session_id": "session", "turn_id": "turn"})
    _event(writer, "agent.usage", base + 4000, actor="review-synth", payload={"usage": {}})
    orch._now = lambda: base + 5000
    orch.run_once(events=[])
    assert not any(e.type == "fanout.synth.completed" for e in log.read_all())

    orch._now = lambda: base + 7601
    orch.run_once(events=[])
    completed = [e for e in log.read_all() if e.type == "fanout.synth.completed"]
    assert completed and completed[-1].payload["liveness_reason"] == "idle"
