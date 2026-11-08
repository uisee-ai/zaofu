"""Exact-target, one-shot recovery of an admitted verifier execution failure."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

ACTION = "redrive_abstained_task_verification"


def recoverable_abstention(
    operation: Mapping[str, Any], events: Sequence[ZfEvent], state_dir: Path
) -> bool:
    if (
        operation.get("status") != "settled"
        or operation.get("task_pipeline_stage") != "verify"
        or operation.get("semantic_verdict") != "abstained"
        or operation.get("verification_recovery_count", 0)
        or not operation.get("call_result_admitted_event_id")
    ):
        return False
    ref = operation.get("admitted_control_result_ref")
    if not isinstance(ref, dict):
        return False
    try:
        result = hydrate_sidecar_ref(state_dir, ref).payload
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(result, dict):
        return False
    if (
        result.get("execution_status") != "failed"
        or result.get("verdict") != "abstained"
        or not result.get("target_commit")
        or any(
            result.get(k) != operation.get(k)
            for k in (
                "operation_id",
                "request_hash",
                "workflow_run_id",
                "task_id",
                "operation_generation",
            )
        )
    ):
        return False
    from zf.core.task.store import TaskStore
    from zf.core.state.task_attempts import TaskAttemptStore

    task = TaskStore(state_dir / "kanban.json").get(str(operation.get("task_id") or ""))
    if task is None or task.status not in {"backlog", "in_progress"}:
        return False
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts

    context = task_pipeline_generation_contexts(events).get(task.id)
    if not context or any(
        context.get(key) != operation.get(key)
        for key in (
            "workflow_run_id",
            "task_map_generation",
            "plan_artifact_package_digest",
        )
    ):
        return False
    if any(
        row.get("task_id") == task.id
        and row.get("status") in {"prepared", "delivering", "sent"}
        for row in TaskAttemptStore(state_dir / "task_attempts.json").current_rows()
    ):
        return False
    from zf.runtime.workflow_operation import reduce_workflow_operations
    from zf.runtime.run_admission import fold_terminal_run_scope

    aliases, terminal_runs = fold_terminal_run_scope(events)
    run = str(operation.get("workflow_run_id") or "")
    if aliases.get(run, run) in terminal_runs:
        return False
    rows = reduce_workflow_operations(events)
    impl = None
    for other in rows.values():
        if (
            other.get("task_id") != operation.get("task_id")
            or other.get("workflow_run_id") != run
        ):
            continue
        if other.get("operation_id") == operation.get("operation_id"):
            continue
        if other.get("status") in {"requested", "running", "reserved", "suspended"}:
            return False
        if int(other.get("operation_generation") or 0) > int(
            operation.get("operation_generation") or 0
        ):
            return False
        if (
            other.get("task_pipeline_stage") == "impl"
            and other.get("task_map_generation") == operation.get("task_map_generation")
            and other.get("operation_generation")
            == operation.get("operation_generation")
        ):
            impl = other
    if (
        not impl
        or impl.get("status") != "settled"
        or impl.get("semantic_verdict") != "passed"
    ):
        return False
    source_id = (impl.get("admitted_control_result_ref") or {}).get("source_event_id")
    source = next((e for e in events if e.id == source_id), None)
    failed = next((e for e in events if e.id == ref.get("source_event_id")), None)
    return bool(
        source
        and source.type == "dev.build.done"
        and source.payload.get("source_commit") == result["target_commit"]
        and source.payload.get("operation_id") == impl.get("operation_id")
        and failed
        and failed.type == "task.pipeline.verify.failed"
        and failed.payload.get("operation_id") == operation.get("operation_id")
        and failed.payload.get("target_commit") == result["target_commit"]
    )
