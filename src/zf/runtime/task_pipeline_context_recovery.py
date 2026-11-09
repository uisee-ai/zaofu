"""Keep Task Pipeline context rotation with the provider lifecycle owner."""

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations


def lifecycle_owns_context_recovery(runtime: Any, event: ZfEvent) -> bool:
    """Keep both reader and task-stage provider rotations out of task audit."""
    from zf.runtime.reader_context_recovery import reader_fanout_owns_context_recovery

    return (reader_fanout_owns_context_recovery(runtime, event)
            or task_pipeline_owns_context_recovery(runtime, event))


def task_pipeline_owns_context_recovery(runtime: Any, event: ZfEvent) -> bool:
    """Prove the original context trigger belongs to a running Impl/Verify.

    A delayed pre-upgrade audit schedule cannot requeue a replacement attempt.
    Match the operation at the trigger's ledger position, not a later owner.
    Completion/result admission and transport failure recovery stay unchanged.
    """
    if event.origin != "kernel":
        return False
    events = runtime.event_log.read_all()
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.type in {"task.retry_scheduled", "task.continuation_scheduled"}:
        trigger_id = str(payload.get("trigger_event_id") or "")
        trigger = next((item for item in events if item.id == trigger_id), None)
        if (trigger is None or trigger.task_id != event.task_id
                or trigger.correlation_id != event.correlation_id
                or (payload.get("dispatch_id")
                    and payload["dispatch_id"] != (trigger.payload or {}).get("dispatch_id"))):
            return False
        event = trigger
        payload = event.payload if isinstance(event.payload, dict) else {}
    if event.type != "worker.context.critical" or event.origin != "kernel":
        return False
    dispatch_id = str(payload.get("dispatch_id") or "")
    if (not event.task_id or not event.correlation_id or not dispatch_id
            or payload.get("task_id", event.task_id) != event.task_id
            or any(payload.get(key) and payload[key] != event.correlation_id
                   for key in ("workflow_run_id", "run_id"))):
        return False
    position = next((i for i, item in enumerate(events) if item.id == event.id), None)
    if position is None:
        return False
    owners = [operation for operation in reduce_workflow_operations(events[:position]).values()
        if operation.get("status") == "running"
        and operation.get("task_pipeline_stage") in {"impl", "verify"}
        and operation.get("task_id") == event.task_id
        and operation.get("workflow_run_id") == event.correlation_id
        and operation.get("dispatch_id") == dispatch_id
        and operation.get("role_instance") == event.actor]
    return len(owners) == 1
