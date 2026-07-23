from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.simulation_lifecycle import (
    emit_simulation_done,
    validate_simulation_scope,
)


def test_simulation_scope_is_limited_to_direct_tmp_zf_root() -> None:
    assert validate_simulation_scope(
        Path("/tmp/zf-light-proof"),
        Path("/tmp/zf-light-proof/.zf"),
    ) == ""
    assert "must match" in validate_simulation_scope(
        Path("/home/user/workspace/project"),
        Path("/home/user/workspace/project/.zf"),
    )
    assert "must be inside" in validate_simulation_scope(
        Path("/tmp/zf-light-proof"),
        Path("/tmp/other-state"),
    )


def test_simulation_done_is_terminal_bound_and_exactly_once(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    terminal = ZfEvent(
        id="run-terminal-1",
        type="run.goal.completed",
        correlation_id="RUN-1",
        payload={"run_id": "RUN-1"},
    )
    log.append(terminal)

    first = emit_simulation_done(terminal, events=log.read_all(), writer=writer)
    second = emit_simulation_done(terminal, events=log.read_all(), writer=writer)

    assert first is not None
    assert second is None
    done = [event for event in log.read_all() if event.type == "simulation.done"]
    assert len(done) == 1
    assert done[0].payload == {
        "schema_version": "simulation-lifecycle.v1",
        "run_id": "RUN-1",
        "status": "completed",
        "terminal_event_id": "run-terminal-1",
        "terminal_event_type": "run.goal.completed",
    }


def test_non_terminal_event_does_not_finish_simulation(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    event = ZfEvent(type="task.done", payload={"run_id": "RUN-1"})
    assert emit_simulation_done(
        event,
        events=[event],
        writer=EventWriter(log),
    ) is None
