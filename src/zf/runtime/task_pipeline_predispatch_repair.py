"""Recover a narrowly identified pre-dispatch result-protocol defect."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


REPAIR_REASON = "missing_flow_kind_before_dispatch"


def reconcile_missing_flow_kind_requests(
    runtime: Any, *, generation_contexts: Mapping[str, Mapping[str, Any]],
) -> None:
    """Supersede only an unsent request proven to have failed this exact way."""
    events = runtime.event_log.read_all()
    operations = reduce_workflow_operations(events)
    if not operations:
        return
    attempts = task_attempt_store(runtime).current_rows()
    service = WorkflowOperationService(
        state_dir=Path(runtime.state_dir), event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    for operation in operations.values():
        task_id = str(operation.get("task_id") or "")
        context = generation_contexts.get(task_id) or {}
        stage = str(operation.get("task_pipeline_stage") or "")
        operation_id = str(operation.get("operation_id") or "")
        if (stage != "impl" or not context.get("flow_kind")
                or operation.get("status") != "requested"
                or operation.get("workflow_run_id") != context.get("workflow_run_id")
                or operation.get("task_map_generation") != context.get("task_map_generation")
                or any(row.get("operation_id") == operation_id for row in attempts)):
            continue
        failure = next((event for event in reversed(events)
            if event.type == "task.pipeline.stage.dispatch_deferred"
            and event.origin == "kernel" and event.actor == "orchestrator"
            and event.task_id == task_id
            and event.payload.get("workflow_run_id") == context.get("workflow_run_id")
            and event.payload.get("task_map_generation") == context.get("task_map_generation")
            and event.payload.get("task_pipeline_stage") == stage
            and int(event.payload.get("operation_generation") or 0)
                == int(operation.get("operation_generation") or 0)
            and event.payload.get("reason")
                == f"ValueError: Task Pipeline {stage} requires a semantic result submit command"), None)
        if failure is None:
            continue
        try:
            body = hydrate_sidecar_ref(Path(runtime.state_dir), operation["request_ref"]).payload
            request = body["request"]
        except (KeyError, TypeError, ValueError, OSError):
            continue
        if (body.get("operation_id") != operation_id
                or body.get("task_id") != task_id
                or body.get("workflow_run_id") != context.get("workflow_run_id")
                or request.get("task_pipeline_stage") != stage
                or request.get("task_map_generation") != context.get("task_map_generation")
                or int(request.get("operation_generation") or 0)
                    != int(operation.get("operation_generation") or 0)
                or request.get("semantic_result_submit_mode") != "off"
                or request.get("result_identity", {}).get("flow_kind")):
            continue
        service.supersede(
            operation_id=operation_id, request_hash=str(operation.get("request_hash") or ""),
            workflow_run_id=str(operation.get("workflow_run_id") or ""),
            task_id=task_id, reason=REPAIR_REASON, causation_id=failure.id,
            correlation_id=str(operation.get("workflow_run_id") or ""),
        )
