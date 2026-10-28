"""Explicit operator intent for retrying a settled, blocked Impl operation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import reduce_workflow_operations

SOURCE = "task_pipeline_operator_retry"


def _retryable_operation(
    operation_id: str,
    operations: Mapping[str, dict[str, Any]],
    contexts: Mapping[str, dict[str, Any]],
    events: list[ZfEvent],
) -> tuple[dict[str, Any], ZfEvent]:
    operation = operations.get(operation_id)
    if not operation or operation.get("task_pipeline_stage") != "impl":
        raise ValueError("target must be a Task Pipeline Impl operation")
    task_id = str(operation.get("task_id") or "")
    context = contexts.get(task_id, {})
    if not context or any(
        operation.get(k) != context.get(k)
        for k in ("workflow_run_id", "task_map_generation")
    ):
        raise ValueError("operation does not belong to the current task generation")
    if (
        operation.get("status") != "settled"
        or operation.get("semantic_verdict") not in {"blocked", "failed"}
    ):
        raise ValueError("only a settled, admitted blocked Impl result can be retried")
    from zf.runtime.run_admission import fold_terminal_run_scope

    aliases, terminal_runs = fold_terminal_run_scope(events)
    run_id = str(operation.get("workflow_run_id") or "")
    if aliases.get(run_id, run_id) in terminal_runs:
        raise ValueError("terminal run cannot be retried")
    for other in operations.values():
        if other.get("task_id") == task_id and other.get("status") in {
            "requested",
            "reserved",
            "running",
            "suspended",
        }:
            raise ValueError("an active operation already owns the task")
        if (
            other.get("task_id") == task_id
            and other.get("workflow_run_id") == operation.get("workflow_run_id")
            and other.get("task_map_generation") == operation.get("task_map_generation")
            and other.get("task_pipeline_stage") == "impl"
            and other.get("operation_id") != operation_id
            and int(other.get("operation_generation") or 0)
            >= int(operation.get("operation_generation") or 0)
        ):
            raise ValueError("a newer Impl operation already owns the task")
    source_id = (operation.get("admitted_control_result_ref") or {}).get(
        "source_event_id"
    )
    source = next((e for e in events if e.id == source_id), None)
    if (
        not operation.get("call_result_admitted_event_id")
        or source is None
        or source.type != "dev.blocked"
        or source.payload.get("operation_id") != operation_id
    ):
        raise ValueError("blocked result lacks an admitted canonical source")
    return operation, source


def request_impl_retry(
    *,
    state_dir: Path,
    event_log: EventLog,
    event_writer: EventWriter,
    operation_id: str,
    reason: str,
    apply: bool = False,
    config: Any = None,
) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("operator repair reason is required")
    with locked_path(state_dir / "locks" / "task-pipeline-operator-retry.lock"):
        events = event_log.read_all()
        operations = reduce_workflow_operations(events)
        contexts = task_pipeline_generation_contexts(events)
        operation, source = _retryable_operation(
            operation_id,
            operations,
            contexts,
            events,
        )
        task_id = operation["task_id"]
        task = TaskStore(state_dir / "kanban.json").get(task_id)
        if task is None or task.status not in {"in_progress", "backlog"}:
            raise ValueError("task is not eligible for Impl retry")
        if any(
            row.get("task_id") == task_id
            and row.get("status") in {"prepared", "delivering", "sent"}
            for row in TaskAttemptStore(state_dir / "task_attempts.json").current_rows()
        ):
            raise ValueError("TaskAttempt must settle before operator retry")
        from zf.runtime.task_pipeline_reconciler import task_pipeline_policy

        policy = task_pipeline_policy(
            config, flow_kind=str(contexts[task_id].get("flow_kind") or "")
        )
        if policy and policy.get("mode") == "blocking":
            repair_offset = sum(
                1 for row in operations.values()
                if row.get("task_id") == task_id
                and row.get("workflow_run_id") == operation.get("workflow_run_id")
                and row.get("task_map_generation") == operation.get("task_map_generation")
                and row.get("status") == "superseded"
                and row.get("reason") == "missing_flow_kind_before_dispatch"
            )
            limit = int(policy.get("max_rework_attempts") or 0) + repair_offset
            if int(operation.get("operation_generation") or 0) > limit:
                raise ValueError(
                    "Impl retry budget exhausted; a controlled new generation is required"
                )
        for event in events:
            if (
                event.type == "task.rework.requested"
                and event.origin == "kernel"
                and event.payload.get("source") == SOURCE
                and event.payload.get("operation_id") == operation_id
            ):
                return {
                    "status": "already_requested",
                    "event_id": event.id,
                    "operation_id": operation_id,
                }
        result = {
            "status": "preview",
            "operation_id": operation_id,
            "task_id": task_id,
            "next_operation_generation": int(operation["operation_generation"]) + 1,
            "source_event_id": source.id,
            "reason": reason.strip(),
        }
        if apply:
            event = event_writer.append(
                ZfEvent(
                    type="task.rework.requested",
                    actor="zf-cli",
                    origin="kernel",
                    task_id=task_id,
                    causation_id=source.id,
                    correlation_id=operation["workflow_run_id"],
                    payload={
                        "source": SOURCE,
                        "operation_id": operation_id,
                        "task_id": task_id,
                        "workflow_run_id": operation["workflow_run_id"],
                        "task_map_generation": operation["task_map_generation"],
                        "operation_generation": operation["operation_generation"],
                        "source_event_id": source.id,
                        "reason": reason.strip(),
                    },
                )
            )
            result.update(status="requested", event_id=event.id)
        return result


def operator_impl_retry_requests(
    *,
    events: list[ZfEvent],
    operations: Mapping[str, dict[str, Any]],
    contexts: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    requests = {}
    for event in events:
        p = event.payload
        if (
            event.type != "task.rework.requested"
            or event.origin != "kernel"
            or p.get("source") != SOURCE
        ):
            continue
        try:
            operation, source = _retryable_operation(
                str(p.get("operation_id") or ""), operations, contexts, events
            )
        except ValueError:
            continue
        if (
            event.task_id != operation["task_id"]
            or p.get("source_event_id") != source.id
            or any(
                p.get(k) != operation.get(k)
                for k in (
                    "workflow_run_id",
                    "task_map_generation",
                    "operation_generation",
                )
            )
        ):
            continue
        requests[operation["task_id"]] = {
            "fault": SOURCE,
            "event_id": event.id,
            "source_event_id": source.id,
            "operation_id": operation["operation_id"],
            "operation_generation": operation["operation_generation"],
            "reason": p.get("reason"),
            "expected_action": "resume_impl_after_operator_repair",
        }
    return requests
