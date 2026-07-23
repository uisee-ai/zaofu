"""Explicit lifecycle support for isolated temporary E2E simulations."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


SIMULATION_TERMINAL_EVENTS = frozenset({"run.goal.completed", "run.goal.blocked"})


def validate_simulation_scope(project_root: Path, state_dir: Path) -> str:
    root = Path(project_root).resolve()
    state = Path(state_dir).resolve()
    if root.parent != Path("/tmp") or not root.name.startswith("zf-"):
        return "simulation project root must match /tmp/zf-*"
    if not state.is_relative_to(root):
        return "simulation state_dir must be inside its /tmp/zf-* project root"
    return ""


def emit_simulation_done(
    terminal: ZfEvent,
    *,
    events: Sequence[ZfEvent],
    writer: EventWriter,
) -> ZfEvent | None:
    if terminal.type not in SIMULATION_TERMINAL_EVENTS:
        return None
    payload = terminal.payload if isinstance(terminal.payload, dict) else {}
    run_id = str(
        payload.get("run_id")
        or payload.get("workflow_run_id")
        or terminal.correlation_id
        or ""
    )
    if any(
        event.type == "simulation.done"
        and isinstance(event.payload, dict)
        and (
            str(event.payload.get("terminal_event_id") or "") == terminal.id
            or (run_id and str(event.payload.get("run_id") or "") == run_id)
        )
        for event in events
    ):
        return None
    return writer.append(ZfEvent(
        type="simulation.done",
        actor="zf-cli",
        causation_id=terminal.id,
        correlation_id=terminal.correlation_id or run_id or None,
        payload={
            "schema_version": "simulation-lifecycle.v1",
            "run_id": run_id,
            "status": "completed" if terminal.type == "run.goal.completed" else "blocked",
            "terminal_event_id": terminal.id,
            "terminal_event_type": terminal.type,
        },
    ))


__all__ = [
    "SIMULATION_TERMINAL_EVENTS",
    "emit_simulation_done",
    "validate_simulation_scope",
]
