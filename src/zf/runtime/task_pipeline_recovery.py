"""Recovery ownership and same-operation redrive for Task Pipeline v4."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TASK_ATTEMPT_IDENTITY_OPERATION_V2
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


TASK_PIPELINE_FAULT_MATRIX_SCHEMA = "task-pipeline-fault-matrix.v1"


def inject_task_stage_recovery(runtime: Any, role: Any) -> bool | None:
    """Use the current stage contract, or wait for admitted redrive without a prompt."""
    from zf.core.safety import PathGuard
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.injection import build_task_prompt
    from zf.runtime.transport import DispatchContext

    registry = RoleSessionRegistry(
        Path(runtime.state_dir) / "role_sessions.yaml", str(runtime.project_root),
    )
    key = registry.instance_meta().get(role.instance_id, {}).get("active_task_stage_binding_key")
    if not key:
        return None
    binding = registry.task_stage_bindings().get(str(key), {})
    if binding.get("status") != "active" or binding.get("current_role_instance") != role.instance_id:
        return False
    events = runtime.event_log.read_all()
    operations = [row for row in reduce_workflow_operations(events).values()
                  if row.get("task_stage_session_binding") == key
                  and row.get("role_instance") == role.instance_id
                  and row.get("status") in {"running", "suspended"}]
    if len(operations) != 1:
        return False
    [operation] = operations
    if operation["status"] == "suspended":
        runtime.event_writer.append(ZfEvent(
            type="worker.recovery.skipped", actor=role.instance_id,
            task_id=operation["task_id"], correlation_id=operation["workflow_run_id"],
            payload={"reason": "task_pipeline_waiting_for_redrive",
                     "operation_id": operation["operation_id"]},
        ))
        return True
    attempt = task_attempt_store(runtime).get(operation["active_attempt_id"])
    if not attempt or attempt.get("status") != "sent":
        return False
    dispatch = next((event for event in reversed(events)
                     if event.type == "task.pipeline.stage.dispatched" and event.origin == "kernel"
                     and event.payload.get("operation_id") == operation["operation_id"]
                     and event.payload.get("attempt_id") == operation["active_attempt_id"]
                     and event.payload.get("dispatch_id") == operation["dispatch_id"]
                     and event.payload.get("task_stage_session_binding") == key
                     and event.payload.get("role_instance") == role.instance_id), None)
    if dispatch is None:
        return False
    path = Path(str(dispatch.payload.get("briefing") or ""))
    PathGuard.assert_under(path, Path(runtime.state_dir))
    if not path.is_file():
        return False
    # This is a continuation of the sent attempt, not a new scheduler dispatch.
    context = DispatchContext(
        role_name=role.name, instance_id=role.instance_id, backend=role.backend,
        briefing_path=path, task_id=operation["task_id"],
        trace_id=operation["workflow_run_id"], run_id=operation["workflow_run_id"],
        operation_id=operation["operation_id"],
    )
    runtime._send_transport_task(
        role.instance_id, path, build_task_prompt(role.instance_id, path, "task_pipeline_stage"), context,
    )
    runtime.event_writer.append(ZfEvent(
        type="worker.recovery.injected", actor=role.instance_id,
        task_id=operation["task_id"], correlation_id=operation["workflow_run_id"],
        payload={"reason": "task_pipeline_current_stage", "briefing_path": str(path),
                 "operation_id": operation["operation_id"], "attempt_id": operation["active_attempt_id"]},
    ))
    return True


_FAULT_CONTRACTS: dict[str, dict[str, Any]] = {
    "pane_dead": {
        "decision_owner": "run_manager",
        "effect_owner": "workflow_runtime_coordinator",
        "effect": "same_operation_redrive",
        "operation_identity": "preserve",
        "attempt_identity": "increment",
    },
    "lease_expired": {
        "decision_owner": "run_manager",
        "effect_owner": "workflow_runtime_coordinator",
        "effect": "same_operation_redrive",
        "operation_identity": "preserve",
        "attempt_identity": "increment",
    },
    "provider_stop": {
        "decision_owner": "run_manager",
        "effect_owner": "workflow_runtime_coordinator",
        "effect": "same_operation_redrive",
        "operation_identity": "preserve",
        "attempt_identity": "increment",
    },
    "wrc_restart": {
        "decision_owner": "kernel_replay",
        "effect_owner": "workflow_runtime_coordinator",
        "effect": "level_triggered_reconcile",
        "operation_identity": "preserve",
        "attempt_identity": "preserve_unless_redrive_admitted",
    },
    "candidate_head_cas_mismatch": {
        "decision_owner": "run_manager",
        "effect_owner": "candidate_integrator",
        "effect": "fail_closed_needs_review",
        "operation_identity": "preserve",
        "attempt_identity": "preserve",
    },
    "late_result": {
        "decision_owner": "kernel_admission",
        "effect_owner": "kernel_admission",
        "effect": "reject_stale_result",
        "operation_identity": "preserve",
        "attempt_identity": "preserve",
    },
    "cancel": {
        "decision_owner": "kernel_admission",
        "effect_owner": "workflow_runtime_coordinator",
        "effect": "terminal_cancel",
        "operation_identity": "preserve",
        "attempt_identity": "supersede",
    },
    "semantic_rework": {
        "decision_owner": "admitted_stage_verdict",
        "effect_owner": "workflow_runtime_coordinator",
        "effect": "new_operation_generation",
        "operation_identity": "increment_generation",
        "attempt_identity": "new",
    },
}


def task_pipeline_fault_contract(fault: str) -> dict[str, Any]:
    """Return the immutable owner/effect contract for one typed fault."""

    contract = _FAULT_CONTRACTS.get(str(fault or "").strip())
    if contract is None:
        raise ValueError(f"unsupported Task Pipeline fault {fault!r}")
    return {"fault": fault, **contract}


def reconcile_task_pipeline_redrives(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> list[WorkflowRuntimeDecision]:
    """Apply only exact, completed Run Manager recovery decisions."""

    events = runtime.event_log.read_all()
    operations = reduce_workflow_operations(events)
    service = WorkflowOperationService(
        state_dir=Path(runtime.state_dir),
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    decisions: list[WorkflowRuntimeDecision] = []
    attempts = sorted(
        task_attempt_store(runtime).current_rows(),
        key=lambda row: str(row.get("attempt_id") or ""),
    )
    for attempt in attempts:
        if not _eligible_attempt(attempt, generation_contexts):
            continue
        operation_id = str(attempt.get("operation_id") or "")
        operation = operations.get(operation_id)
        if operation is None or str(operation.get("status") or "") != "suspended":
            continue
        request = _completed_run_manager_recovery(
            events,
            attempt=attempt,
        )
        if request is None:
            continue
        event = service.admit_redrive(
            operation_id=operation_id,
            request_hash=str(operation.get("request_hash") or ""),
            workflow_run_id=str(attempt.get("run_id") or ""),
            task_id=str(attempt.get("task_id") or ""),
            source_attempt_id=str(attempt.get("attempt_id") or ""),
            recovery_decision_event_id=request.id,
            reason="Run Manager recovery completed; WRC redrive admitted",
        )
        if event is not None:
            decisions.append(WorkflowRuntimeDecision(
                action="task_pipeline_operation_redrive_admitted",
                task_id=str(attempt.get("task_id") or ""),
                reason=(
                    f"same operation {operation_id} admitted for a new attempt"
                ),
            ))
    return decisions


def project_task_pipeline_recovery(
    *,
    events: Iterable[ZfEvent],
    attempts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    event_rows = list(events)
    attempt_rows = [dict(row) for row in attempts]
    return {
        "schema_version": TASK_PIPELINE_FAULT_MATRIX_SCHEMA,
        "fault_contracts": [
            task_pipeline_fault_contract(fault)
            for fault in sorted(_FAULT_CONTRACTS)
        ],
        "pending_run_manager_attempt_ids": sorted(
            str(row.get("attempt_id") or "")
            for row in attempt_rows
            if str(row.get("identity_version") or "")
            == TASK_ATTEMPT_IDENTITY_OPERATION_V2
            and str(row.get("status") or "") == "expired"
            and str(row.get("recovery_owner") or "") == "run_manager"
        ),
        "admitted_redrives": [{
            "event_id": event.id,
            "operation_id": str(_payload(event).get("operation_id") or ""),
            "source_attempt_id": str(
                _payload(event).get("source_attempt_id") or ""
            ),
            "decision_owner": "run_manager",
            "effect_owner": "workflow_runtime_coordinator",
        } for event in event_rows
            if event.type == "workflow.operation.redrive_admitted"],
        "supervisor_authority": "read_only_observer",
        "run_manager_authority": "unique_recovery_decision_owner",
        "wrc_authority": "frozen_mechanical_effect_executor",
    }


def _eligible_attempt(
    row: Mapping[str, Any],
    contexts: Mapping[str, Mapping[str, Any]],
) -> bool:
    task_id = str(row.get("task_id") or "")
    context = contexts.get(task_id)
    return bool(
        context
        and str(row.get("identity_version") or "")
        == TASK_ATTEMPT_IDENTITY_OPERATION_V2
        and str(row.get("status") or "") == "expired"
        and str(row.get("recovery_owner") or "") == "run_manager"
        and str(row.get("run_id") or "")
        == str(context.get("workflow_run_id") or "")
    )


def _completed_run_manager_recovery(
    events: list[ZfEvent],
    *,
    attempt: Mapping[str, Any],
) -> ZfEvent | None:
    operation_id = str(attempt.get("operation_id") or "")
    attempt_id = str(attempt.get("attempt_id") or "")
    task_id = str(attempt.get("task_id") or "")
    requests = [
        event for event in events
        if event.type == "worker.respawn.requested"
        and str(event.actor or "") == "run-manager"
        and str(event.task_id or _payload(event).get("task_id") or "")
        == task_id
        and str(_payload(event).get("operation_id") or "") == operation_id
        and str(_payload(event).get("attempt_id") or "") == attempt_id
        and str(_payload(event).get("recovery_decision_owner") or "")
        == "run_manager"
    ]
    for request in reversed(requests):
        if any(
            event.type == "worker.respawn.completed"
            and (
                str(event.causation_id or "") == request.id
                or str(_payload(event).get("request_id") or "") == request.id
            )
            for event in events
        ):
            return request
    return None


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "TASK_PIPELINE_FAULT_MATRIX_SCHEMA",
    "project_task_pipeline_recovery",
    "reconcile_task_pipeline_redrives",
    "task_pipeline_fault_contract",
]
