"""Operator retry after an admitted Verify execution failure, never a product verdict."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import json

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.store import TaskStore
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.run_admission import run_dispatch_block_reason
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)

REASON_PREFIX = "operator_verify_execution_retry: "


def request_verify_retry(
    *,
    state_dir: Path,
    event_log: EventLog,
    event_writer: EventWriter,
    operation_id: str,
    reason: str,
    apply: bool = False,
) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("operator repair reason is required")
    with locked_path(state_dir / "locks" / "task-pipeline-verify-retry.lock"):
        events = event_log.read_all()
        operations = reduce_workflow_operations(events)
        op = operations.get(operation_id, {})
        if op.get("task_pipeline_stage") != "verify":
            raise ValueError("target must be a Task Pipeline Verify operation")
        task_id, run_id = (
            str(op.get("task_id") or ""),
            str(op.get("workflow_run_id") or ""),
        )
        context = task_pipeline_generation_contexts(events).get(task_id, {})
        if not context or any(
            op.get(k) != context.get(k)
            for k in ("workflow_run_id", "task_map_generation")
        ):
            raise ValueError("operation does not belong to the current task generation")
        for event in events:
            if (
                event.type == "workflow.operation.superseded"
                and event.origin == "kernel"
                and event.payload.get("operation_id") == operation_id
                and str(event.payload.get("reason") or "").startswith(REASON_PREFIX)
            ):
                return {
                    "status": "already_requested",
                    "operation_id": operation_id,
                    "event_id": event.id,
                }
        if is_dispatch_paused(state_dir, events=events):
            raise ValueError("dispatch is paused")
        blocked = run_dispatch_block_reason(
            SimpleNamespace(event_log=event_log), run_id=run_id
        )
        if blocked:
            raise ValueError(blocked)
        if op.get("status") != "settled" or op.get("semantic_verdict") != "abstained":
            raise ValueError(
                "only a settled Verify execution failure without a product verdict can be retried"
            )
        descriptor = op.get("admitted_control_result_ref") or {}
        if not op.get("call_result_admitted_event_id"):
            raise ValueError("Verify result must be admitted")
        body = hydrate_sidecar_ref(state_dir, descriptor).payload
        if (
            not isinstance(body, dict)
            or body.get("schema_version") != "verification-result.v1"
            or body.get("execution_status") != "failed"
            or body.get("verdict") != "abstained"
            or any(
                body.get(k) != op.get(k)
                for k in (
                    "operation_id",
                    "task_id",
                    "workflow_run_id",
                    "task_map_generation",
                    "operation_generation",
                )
            )
            or not body.get("request_hash")
            or body["request_hash"] != op.get("request_hash")
            or not body.get("attempt_id")
            or body["attempt_id"] != op.get("active_attempt_id")
        ):
            raise ValueError(
                "Verify execution failure evidence is missing or mismatched"
            )
        source = next(
            (e for e in events if e.id == descriptor.get("source_event_id")), None
        )
        if (
            source is None
            or source.type != "task.pipeline.verify.failed"
            or source.task_id != task_id
            or source.payload.get("operation_id") != operation_id
        ):
            raise ValueError(
                "Verify execution failure lacks its canonical source event"
            )
        task = TaskStore(state_dir / "kanban.json").get(task_id)
        if task is None or task.status not in {"backlog", "in_progress"}:
            raise ValueError("task is not eligible for Verify retry")
        identity = current_task_contract_identity(task)
        if any(
            not body.get(k) or body[k] != identity.get(k)
            for k in (
                "task_id",
                "workflow_run_id",
                "contract_revision",
                "task_map_generation",
            )
        ):
            raise ValueError(
                "Verify result no longer matches the current Task contract"
            )
        target = str(body.get("target_commit") or "")
        index = json.loads((state_dir / "refs" / "task-index.json").read_text())
        if not target or index.get(task_id, {}).get("source_commit") != target:
            raise ValueError("Verify target is no longer the accepted TaskRef")
        generation = int(op.get("operation_generation") or 1)
        for row in operations.values():
            if row.get("task_id") != task_id or row.get("operation_id") == operation_id:
                continue
            if row.get("status") in {"requested", "reserved", "running", "suspended"}:
                raise ValueError("an active operation already owns the task")
            if (
                row.get("workflow_run_id") == run_id
                and row.get("task_map_generation") == op.get("task_map_generation")
                and (
                    int(row.get("operation_generation") or 1) > generation
                    or (
                        row.get("task_pipeline_stage") == "verify"
                        and int(row.get("operation_generation") or 1) == generation
                    )
                )
            ):
                raise ValueError("a newer stage supersedes this Verify")
        if any(
            row.get("task_id") == task_id
            and row.get("status") in {"prepared", "delivering", "sent"}
            for row in TaskAttemptStore(state_dir / "task_attempts.json").current_rows()
        ):
            raise ValueError("TaskAttempt must settle before Verify retry")
        result = {
            "status": "preview",
            "operation_id": operation_id,
            "task_id": task_id,
            "next_operation_generation": generation + 1,
            "source_event_id": source.id,
            "target_commit": target,
            "reason": reason.strip(),
        }
        if apply:
            event = WorkflowOperationService(
                state_dir=state_dir, event_log=event_log, event_writer=event_writer
            ).supersede(
                operation_id=operation_id,
                request_hash=str(op.get("request_hash") or ""),
                workflow_run_id=run_id,
                task_id=task_id,
                reason=REASON_PREFIX + reason.strip(),
                causation_id=source.id,
                correlation_id=run_id,
            )
            result.update(status="requested", event_id=event.id if event else "")
        return result


def verify_impl_generation(
    events: list[ZfEvent],
    *,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    operation_generation: int,
) -> int:
    """Reuse an Impl generation only across consecutive authorized Verify retries."""
    rows = [
        row
        for row in reduce_workflow_operations(events).values()
        if row.get("task_id") == task_id
        and row.get("workflow_run_id") == workflow_run_id
        and row.get("task_map_generation") == task_map_generation
    ]
    impl = max(
        (row for row in rows if row.get("task_pipeline_stage") == "impl"),
        key=lambda row: int(row.get("operation_generation") or 1),
        default={},
    )
    generation = int(impl.get("operation_generation") or operation_generation)
    if (
        generation >= operation_generation
        or impl.get("status") != "settled"
        or impl.get("semantic_verdict") != "passed"
        or not impl.get("call_result_admitted_event_id")
    ):
        return operation_generation
    for step in range(generation, operation_generation):
        verify = [
            row
            for row in rows
            if row.get("task_pipeline_stage") == "verify"
            and int(row.get("operation_generation") or 1) == step
        ]
        if (
            len(verify) != 1
            or verify[0].get("status") != "superseded"
            or verify[0].get("semantic_verdict") != "abstained"
            or not str(verify[0].get("reason") or "").startswith(REASON_PREFIX)
        ):
            return operation_generation
    return generation
