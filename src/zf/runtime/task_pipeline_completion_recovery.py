"""Keep completion-audit requeue consistent with its owning operation."""

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.task_attempt_operation_settlement import settle_terminal_operation_attempt
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def interrupt_completion_operation(
    runtime: Any, event: ZfEvent, *, task_id: str, dispatch_id: str,
) -> None:
    """Suspend only the dispatched Task Pipeline operation being requeued.

    Retain request identity for controlled resume. Settle its attempt before
    clearing TaskStore's dispatch so late heartbeats/results cannot retain the
    old lease. Replay after a partial interruption also finishes settlement.
    """
    if not dispatch_id:
        return
    events = runtime.event_log.read_all()
    service = WorkflowOperationService(
        state_dir=runtime.state_dir, event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    for operation in reduce_workflow_operations(events).values():
        if (operation.get("task_id") != task_id
                or operation.get("dispatch_id") != dispatch_id
                or not operation.get("task_pipeline_stage")):
            continue
        if operation.get("status") == "running":
            interrupted = service.interrupt(
                operation_id=operation["operation_id"],
                request_hash=operation["request_hash"],
                workflow_run_id=operation["workflow_run_id"], task_id=task_id,
                source_attempt_id=str(operation.get("active_attempt_id") or ""),
                reason="completion_audit_retry", causation_id=event.id,
                correlation_id=operation["workflow_run_id"],
            )
        elif (operation.get("status") == "suspended"
              and operation.get("reason") == "completion_audit_retry"):
            interrupted = next((e for e in reversed(events)
                if e.type == "workflow.operation.interrupted"
                and e.payload.get("operation_id") == operation["operation_id"]
                and e.causation_id == event.id), None)
        else:
            continue
        if interrupted is not None:
            settle_terminal_operation_attempt(runtime, interrupted)
