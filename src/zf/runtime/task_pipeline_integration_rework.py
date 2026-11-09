"""Evidence checks and briefing feedback for reviewed integration-to-Impl recovery."""

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.task_pipeline_entry import task_pipeline_entry_mode
from zf.runtime.task_pipeline_integration_retry import IMPL_REWORK_REASON_PREFIX


def validate_impl_rework(
    state_dir: Path, *, task: Task, operation: Mapping[str, Any],
    verify: Mapping[str, Any], operations: Iterable[Mapping[str, Any]],
    events: Iterable[ZfEvent],
) -> str:
    if task_pipeline_entry_mode(task) != "standard":
        raise ValueError("integration rework requires a standard writer task")
    impl = max(
        (row for row in operations if row.get("task_pipeline_stage") == "impl"),
        key=lambda row: int(row.get("operation_generation") or 1), default={},
    )
    if (impl.get("status") != "settled" or impl.get("semantic_verdict") != "passed"
            or not impl.get("call_result_admitted_event_id")):
        raise ValueError("integration rework requires an admitted passing Impl")
    descriptor = verify.get("admitted_control_result_ref") or {}
    body = hydrate_sidecar_ref(state_dir, descriptor).payload
    if (not isinstance(body, dict) or body.get("schema_version") != "verification-result.v1"
            or body.get("verdict") != "passed"
            or body.get("execution_status") != "completed"
            or any(not body.get(k) or body[k] != verify.get(k) for k in (
                "operation_id", "task_id", "workflow_run_id", "task_map_generation",
                "operation_generation", "request_hash",
            ))
            or not body.get("attempt_id")
            or body["attempt_id"] != verify.get("active_attempt_id")):
        raise ValueError("passing Verify evidence is missing or mismatched")
    source = next((event for event in events if event.id == descriptor.get("source_event_id")), None)
    if (source is None or source.type != "task.pipeline.verify.completed"
            or source.task_id != task.id
            or source.payload.get("operation_id") != verify.get("operation_id")):
        raise ValueError("passing Verify lacks its canonical completion event")
    identity = current_task_contract_identity(task)
    if any(not body.get(k) or body[k] != identity.get(k) for k in (
        "task_id", "workflow_run_id", "contract_revision", "task_map_generation",
    )):
        raise ValueError("Verify result no longer matches the current Task contract")
    target = str(body.get("target_commit") or "")
    index = json.loads((state_dir / "refs" / "task-index.json").read_text())
    if not target or index.get(task.id, {}).get("source_commit") != target:
        raise ValueError("Verify target is no longer the accepted TaskRef")
    if not (int(impl.get("operation_generation") or 1)
            <= int(verify.get("operation_generation") or 1)
            <= int(operation.get("operation_generation") or 1)):
        raise ValueError("integration rework stage generations are inconsistent")
    return target


def integration_rework_feedback(
    operations: Iterable[Mapping[str, Any]], *, task_id: str,
    workflow_run_id: str, task_map_generation: str, operation_generation: int,
) -> list[dict[str, Any]]:
    for row in operations:
        reason = str(row.get("reason") or "")
        if (row.get("task_id") == task_id
                and row.get("workflow_run_id") == workflow_run_id
                and row.get("task_map_generation") == task_map_generation
                and row.get("operation_generation") == operation_generation
                and row.get("task_pipeline_stage") == "integration"
                and row.get("status") == "superseded"
                and reason.startswith(IMPL_REWORK_REASON_PREFIX)):
            return [{
                "source": "operator_integration_impl_rework",
                "reason": reason.removeprefix(IMPL_REWORK_REASON_PREFIX),
                "source_operation_id": row["operation_id"],
                "expected_action": "repair_in_scope_and_resubmit_for_independent_verification",
            }]
    return []
