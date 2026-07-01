"""Worker refresh — policy evaluation and execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RefreshTrigger:
    reason: str  # turns_elapsed, task_complete, drift, context_pressure, failures
    detail: str = ""


class RefreshPolicy:
    """Evaluate whether a worker needs refresh."""

    def __init__(
        self,
        *,
        max_turns: int = 10,
        max_failures: int = 3,
        context_pressure_threshold: float = 0.7,
    ) -> None:
        self.max_turns = max_turns
        self.max_failures = max_failures
        self.context_pressure_threshold = context_pressure_threshold

    def evaluate(
        self,
        turn_count: int = 0,
        consecutive_failures: int = 0,
        task_just_completed: bool = False,
        drift_detected: bool = False,
        context_pressure: float = 0.0,
    ) -> RefreshTrigger | None:
        """Return a trigger if refresh needed, None otherwise."""
        if turn_count >= self.max_turns:
            return RefreshTrigger(
                reason="turns_elapsed",
                detail=f"{turn_count} turns (max {self.max_turns})",
            )
        if task_just_completed:
            return RefreshTrigger(reason="task_complete")
        if drift_detected:
            return RefreshTrigger(reason="drift")
        if context_pressure >= self.context_pressure_threshold:
            return RefreshTrigger(
                reason="context_pressure",
                detail=f"pressure={context_pressure:.0%}",
            )
        if consecutive_failures >= self.max_failures:
            return RefreshTrigger(
                reason="failures",
                detail=f"{consecutive_failures} consecutive failures",
            )
        return None
