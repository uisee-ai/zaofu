"""Mechanical recovery inputs that need not wait for a Supervisor inspection."""
from collections.abc import Iterable

from zf.core.events.model import ZfEvent


RECOVERY_INPUTS = frozenset({
    "runtime.action.attempt.completed",
    "workflow.operation.settled",
    "worker.assistance.requested",
    "run.manager.agent.recommendation",
    "run.manager.reflect.completed",
    "autoresearch.loop.completed",
    "autoresearch.loop.failed",
})


def has_new_recovery_input(events: Iterable[ZfEvent], consumed_id: str) -> bool:
    """Wake once for new recovery facts; ordinary observations remain periodic."""
    for event in reversed(list(events)):
        if event.id == consumed_id:
            return False
        if event.type in RECOVERY_INPUTS:
            return True
    return False
