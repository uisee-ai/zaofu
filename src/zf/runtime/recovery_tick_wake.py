"""Mechanical recovery inputs that need not wait for a Supervisor inspection."""
from collections.abc import Iterable

from zf.core.events.model import ZfEvent


RECOVERY_INPUTS = frozenset({
    "runtime.action.attempt.completed",
    "workflow.operation.settled",
    "provider.turn.closed",
    "worker.assistance.requested",
    "human.escalation.acknowledged",
    "run.manager.agent.recommendation",
    "run.manager.reflect.completed",
    "autoresearch.loop.completed",
    "autoresearch.loop.failed",
})


def is_recovery_input(event: ZfEvent) -> bool:
    if event.type == "workflow.mailbox.question":
        return event.origin == "kernel" and event.actor == "durable-mailbox"
    if event.type == "autoresearch.repair.dispatch_blocked":
        return event.origin == "kernel" and bool(event.payload.get("source_request_id"))
    if event.type in RECOVERY_INPUTS:
        return True
    body = event.payload
    return bool(event.origin == "kernel"
        and event.type in {"run.manager.action.applied", "run.manager.action.failed"}
        and body.get("action") in {"repair-closeout-validate", "repair-closeout-apply", "repair-closeout-dispose"}
        and body.get("queue_id") and body.get("repair_contract_digest"))


def has_new_recovery_input(events: Iterable[ZfEvent], consumed_id: str) -> bool:
    """Wake once for new recovery facts; ordinary observations remain periodic."""
    for event in reversed(list(events)):
        if event.id == consumed_id:
            return False
        if is_recovery_input(event):
            return True
    return False
