"""Mechanical recovery gates shared by pending, agent and operator entrypoints."""

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.recovery_case import recovery_action_admission
from zf.runtime.run_continuation import bind_action_dependencies, human_decisions_block_action


def action_gate_reason(
    state_dir: Path, events: list[ZfEvent], action: dict[str, Any],
    pending_human: list[dict[str, Any]], *, approved_decision_token: str = "",
) -> str:
    if is_dispatch_paused(state_dir, events=events):
        return "dispatch_paused"
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
