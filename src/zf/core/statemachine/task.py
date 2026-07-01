"""Task state machine — pure transition logic, no I/O."""

from __future__ import annotations


class InvalidTransition(Exception):
    pass


# Transition table: from_state -> set of valid to_states
_TRANSITIONS: dict[str, set[str]] = {
    "backlog": {"in_progress", "blocked", "cancelled"},
    "in_progress": {"review", "blocked", "cancelled"},
    "review": {"testing", "cancelled"},
    "testing": {"done", "cancelled"},
    "blocked": {"backlog", "in_progress", "cancelled"},
    "done": set(),
    "cancelled": set(),
}


class TaskStateMachine:
    def can_transition(self, from_state: str, to_state: str) -> bool:
        targets = _TRANSITIONS.get(from_state)
        if targets is None:
            return False
        return to_state in targets

    def transition(self, from_state: str, to_state: str) -> str:
        if not self.can_transition(from_state, to_state):
            raise InvalidTransition(
                f"Cannot transition from '{from_state}' to '{to_state}'"
            )
        return to_state

    def valid_transitions(self) -> dict[str, set[str]]:
        return dict(_TRANSITIONS)
