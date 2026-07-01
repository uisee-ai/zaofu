"""Loop state machine — iteration lifecycle, no I/O."""

from __future__ import annotations

from zf.core.statemachine.task import InvalidTransition

_TRANSITIONS: dict[str, set[str]] = {
    "starting": {"running", "failed", "terminated"},
    "running": {"waiting", "paused", "completed", "failed", "terminated"},
    "waiting": {"running", "paused", "completed", "failed", "terminated"},
    "paused": {"running", "terminated"},
    "recovering": {"running", "failed", "terminated"},
    "completed": set(),  # terminal
    "failed": {"recovering", "terminated"},
    "terminated": set(),  # terminal
}


class LoopStateMachine:
    def can_transition(self, from_state: str, to_state: str) -> bool:
        targets = _TRANSITIONS.get(from_state)
        if targets is None:
            return False
        return to_state in targets

    def transition(self, from_state: str, to_state: str) -> str:
        if not self.can_transition(from_state, to_state):
            raise InvalidTransition(
                f"Loop cannot transition from '{from_state}' to '{to_state}'"
            )
        return to_state

    def valid_transitions(self) -> dict[str, set[str]]:
        return dict(_TRANSITIONS)
