"""Mechanical recovery gates shared by pending, agent and operator entrypoints."""

from pathlib import Path
from functools import wraps
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.core.state.locks import locked_path
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.recovery_case import recovery_action_admission
from zf.runtime.run_continuation import bind_action_dependencies, human_decisions_block_action


def serialized_recovery_action(function):
    @wraps(function)
    def execute(*args, **kwargs):
        with locked_path(Path(kwargs["state_dir"]) / "locks" / "recovery-action-admission"):
            return function(*args, **kwargs)
    return execute


def action_gate_reason(
    state_dir: Path, events: list[ZfEvent], action: dict[str, Any],
    pending_human: list[dict[str, Any]], *, approved_decision_token: str = "",
) -> str:
    if any(event.type == "event.malformed" and event.payload.get("ledger_integrity") == "unknown" for event in events):
        return "event_ledger_integrity_unknown"
    if is_dispatch_paused(state_dir, events=events):
        return "dispatch_paused"
    from zf.runtime.recovery_association import shared_repair_gate
    try:
        if reason := shared_repair_gate(state_dir, events, action):
            return reason
    except (OSError, ValueError, KeyError):
        return "shared_repair_evidence_unavailable"
    gates = [gate for gate in pending_human
             if not approved_decision_token or gate.get("decision_token") != approved_decision_token]
    scoped = bind_action_dependencies([action], TaskStore(state_dir / "kanban.json").list_all())[0]
    if human_decisions_block_action(gates, scoped):
        return "human_decision_pending"
    admission = recovery_action_admission(events, action)
    if admission["status"] != "admitted":
        # A matched operator approval may release a waiting gate, never an
        # already-running or completed effect owned by the same recovery case.
        if not (approved_decision_token and admission["case_status"] in {"waiting", "blocked"}):
            return "recovery_case:" + str(admission["reason"])
    return ""
