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
    """Apply evidence-bound pre-dispatch repair and Run Manager redrives."""

    from zf.runtime.task_pipeline_predispatch_repair import reconcile_missing_flow_kind_requests

    reconcile_missing_flow_kind_requests(runtime, generation_contexts=generation_contexts)

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
    # A watchdog can fail after the pane was replaced but before the Run
    # Manager can persist a normal requested/completed pair (for example,
    # recovery briefing injection fails).  The lifecycle event is admissible
    # only when it carries the complete operation identity and explicitly
    # declares the Run Manager decision/effect owners.  This preserves the
    # same-operation redrive contract while keeping unrelated respawn errors
    # out of the Task Pipeline.
    failures = [
        event for event in events
        if event.type == "worker.respawn.failed"
        and str(event.task_id or _payload(event).get("task_id") or "") == task_id
        and str(_payload(event).get("operation_id") or "") == operation_id
        and str(_payload(event).get("attempt_id") or "") == attempt_id
        and bool(_payload(event).get("recovery_retryable"))
        and bool(_payload(event).get("recovery_context_bound"))
        and str(_payload(event).get("recovery_decision_owner") or "")
        == "run_manager"
        and str(_payload(event).get("recovery_effect_owner") or "")
        == "workflow_runtime_coordinator"
    ]
    if failures:
        return failures[-1]
    return None


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "TASK_PIPELINE_FAULT_MATRIX_SCHEMA",
    "project_task_pipeline_recovery",
    "reconcile_task_pipeline_redrives",
    "task_pipeline_fault_contract",
]
