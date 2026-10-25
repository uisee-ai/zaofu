"""Operator-authorized retry of a reviewed, failed Candidate integration."""

from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.store import TaskStore
from zf.runtime.run_admission import fold_terminal_run_scope
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import (
    TERMINAL_OPERATION_STATUSES,
    WorkflowOperationService,
    reduce_workflow_operations,
)

REASON_PREFIX = "operator_integration_retry: "


def request_integration_retry(
    *, state_dir: Path, event_log: EventLog, event_writer: EventWriter,
    operation_id: str, reason: str, apply: bool = False,
) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("operator repair reason is required")
    with locked_path(state_dir / "locks" / "task-pipeline-integration-retry.lock"):
        events = event_log.read_all()
        operations = reduce_workflow_operations(events)
        operation = operations.get(operation_id, {})
        if operation.get("task_pipeline_stage") != "integration":
            raise ValueError("target must be a Task Pipeline integration operation")
        task_id = str(operation.get("task_id") or "")
        context = task_pipeline_generation_contexts(events).get(task_id, {})
        if not context or any(operation.get(k) != context.get(k) for k in (
            "workflow_run_id", "task_map_generation",
        )):
            raise ValueError("operation does not belong to the current task generation")
        for event in events:
            if (event.type == "workflow.operation.superseded"
                and event.origin == "kernel"
                and event.payload.get("operation_id") == operation_id
                and str(event.payload.get("reason") or "").startswith(REASON_PREFIX)):
                return {"status": "already_requested", "operation_id": operation_id,
                        "event_id": event.id}
        if operation.get("status") != "blocked":
            raise ValueError("only a blocked integration operation can be retried")
        source = next((e for e in reversed(events) if (
            e.type == "integration.queue.needs_review" and e.origin == "kernel"
            and e.task_id == task_id and e.payload.get("operation_id") == operation_id
            and e.payload.get("task_map_generation") == context.get("task_map_generation")
            and e.payload.get("workflow_run_id") == context.get("workflow_run_id")
        )), None)
        if source is None:
            raise ValueError("blocked integration lacks a canonical needs_review event")
        aliases, terminal_runs = fold_terminal_run_scope(events)
        run_id = str(operation.get("workflow_run_id") or "")
        if aliases.get(run_id, run_id) in terminal_runs:
            raise ValueError("terminal run cannot be retried")
        task = TaskStore(state_dir / "kanban.json").get(task_id)
        if task is None or task.status not in {"backlog", "in_progress"}:
            raise ValueError("task is not eligible for integration retry")
        generation = int(operation.get("operation_generation") or 1)
        current = []
        for row in operations.values():
            if row.get("task_id") != task_id:
                continue
            if row.get("status") not in TERMINAL_OPERATION_STATUSES:
                raise ValueError("an active operation already owns the task")
            if any(row.get(k) != operation.get(k) for k in (
                "workflow_run_id", "task_map_generation",
            )):
                continue
            current.append(row)
            if (row.get("operation_id") != operation_id
                and row.get("task_pipeline_stage") == "integration"
                and int(row.get("operation_generation") or 1) >= generation):
                raise ValueError("a newer integration operation already owns the task")
            if (row.get("task_pipeline_stage") in {"impl", "verify", "acceptance_review"}
                and int(row.get("operation_generation") or 1) > generation):
                raise ValueError("a newer stage generation supersedes this integration")
        verify = max((row for row in current if row.get("task_pipeline_stage") == "verify"),
                     key=lambda row: int(row.get("operation_generation") or 1), default={})
        if (verify.get("status") != "settled"
            or verify.get("semantic_verdict") not in {"passed", "pass", "approved", "approve", "admit"}
            or not verify.get("call_result_admitted_event_id")):
            raise ValueError("integration retry requires an admitted passing Verify result")
        if any(row.get("task_id") == task_id and row.get("status") in {
            "prepared", "delivering", "sent",
        } for row in TaskAttemptStore(state_dir / "task_attempts.json").current_rows()):
            raise ValueError("TaskAttempt must settle before integration retry")
        result = {"status": "preview", "operation_id": operation_id, "task_id": task_id,
                  "next_operation_generation": generation + 1,
                  "source_event_id": source.id, "reason": reason.strip()}
        if apply:
            event = WorkflowOperationService(
                state_dir=state_dir, event_log=event_log, event_writer=event_writer,
            ).supersede(
                operation_id=operation_id, request_hash=str(operation.get("request_hash") or ""),
                workflow_run_id=run_id, task_id=task_id,
                reason=REASON_PREFIX + reason.strip(), causation_id=source.id,
                correlation_id=run_id,
            )
            result.update(status="requested", event_id=event.id if event else "")
        return result
