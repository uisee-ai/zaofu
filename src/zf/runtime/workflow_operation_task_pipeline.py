"""Task Pipeline extensions to the replayable WorkflowOperation contract."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent


_BUDGET_AMENDMENT_DIMENSIONS = {
    "wall_clock": ("timeout_seconds", "elapsed_seconds"),
    "tokens": ("token_budget", "total_tokens"),
    "usd": ("cost_budget_usd", "total_usd"),
    "usage_samples": ("max_usage_samples", "usage_samples"),
}


TASK_PIPELINE_REQUEST_KEYS = (
    "task_pipeline_stage",
    "operation_generation",
    "task_map_generation",
    "workspace_generation",
    "placement_epoch",
    "pipeline_key",
    "contract_revision",
    "task_stage_session_binding",
    "risk_class",
    "integration_admission_profile",
    "exact_task_target_commit",
    "verification_result_ref",
    "verification_result_digest",
    "risk_review_timeout_seconds",
    "risk_review_max_turns",
    "risk_review_budget_usd",
)
_INTEGER_REQUEST_KEYS = frozenset({
    "operation_generation",
    "workspace_generation",
    "placement_epoch",
})


def task_pipeline_operation_seed(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_pipeline_stage": str(payload.get("task_pipeline_stage") or ""),
        "operation_generation": _int_value(payload.get("operation_generation")),
        "task_map_generation": str(payload.get("task_map_generation") or ""),
        "workspace_generation": _int_value(payload.get("workspace_generation")),
        "placement_epoch": _int_value(payload.get("placement_epoch")),
        "pipeline_key": str(payload.get("pipeline_key") or ""),
        "task_stage_session_binding": str(
            payload.get("task_stage_session_binding") or ""
        ),
        "admitted_control_result_ref": {},
        "semantic_verdict": "",
        "call_result_admitted_event_id": "",
        "redrive_count": 0,
        "redrive_source_attempt_ids": [],
        "compatibility_proof_digest": "",
    }


def apply_call_result_admission(
    operations: Mapping[str, dict[str, Any]],
    event: ZfEvent,
) -> bool:
    if event.type != "workflow.call.result.admitted":
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    row = operations.get(str(payload.get("operation_id") or ""))
    if row is not None:
        row["semantic_verdict"] = str(payload.get("semantic_verdict") or "")
        control_ref = payload.get("control_result_ref")
        if isinstance(control_ref, Mapping):
            row["admitted_control_result_ref"] = dict(control_ref)
        row["call_result_admitted_event_id"] = event.id
    return True


def task_pipeline_request_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in TASK_PIPELINE_REQUEST_KEYS:
        value = payload.get(key)
        if value in (None, "", 0):
            continue
        result[key] = _int_value(value) if key in _INTEGER_REQUEST_KEYS else str(value)
    return result


def apply_task_pipeline_operation_event(
    row: dict[str, Any],
    event: ZfEvent,
    payload: Mapping[str, Any],
) -> bool:
    if event.type != "workflow.operation.redrive_admitted":
        return False
    cancelled_restart_replay = (
        row.get("status") == "cancelled"
        and row.get("reason")
        == "workflow_resume_cancelled_interrupted_operation"
        and str(payload.get("recovery_decision_owner") or "")
        == "kernel_replay"
        and str(payload.get("recovery_from_status") or "") == "cancelled"
    )
    blocked_compatibility_replay = (
        row.get("status") == "blocked"
        and row.get("reason") in {
            "request_hash_divergence",
            "request_hash_compatibility_failed",
        }
        and str(payload.get("recovery_decision_owner") or "")
        == "kernel_replay"
        and str(payload.get("recovery_from_status") or "") == "blocked"
        and bool(str(payload.get("compatibility_proof_digest") or ""))
    )
    blocked_controlled_integration_retry = (
        row.get("status") == "blocked"
        and str(row.get("task_pipeline_stage") or "") == "integration"
        and str(row.get("reason") or "").startswith(
            "candidate_incremental_failed:"
        )
        and str(payload.get("recovery_decision_owner") or "")
        == "controlled_action"
        and str(payload.get("recovery_from_status") or "") == "blocked"
    )
    blocked_budget_amendment_replay = (
        row.get("status") == "blocked"
        and str(row.get("reason") or "").startswith(
            "workflow_budget_exceeded:"
        )
        and str(payload.get("recovery_decision_owner") or "")
        == "operator_budget_amendment"
        and str(payload.get("recovery_from_status") or "") == "blocked"
    )
    if (
        row.get("status") in {"suspended", "requested"}
        or cancelled_restart_replay
        or blocked_compatibility_replay
        or blocked_controlled_integration_retry
        or blocked_budget_amendment_replay
    ):
        row["status"] = "requested"
        row["divergent"] = False
        row["redrive_count"] = int(row.get("redrive_count") or 0) + 1
        source_attempt_id = str(payload.get("source_attempt_id") or "")
        if source_attempt_id and source_attempt_id not in row[
            "redrive_source_attempt_ids"
        ]:
            row["redrive_source_attempt_ids"].append(source_attempt_id)
        reset_keys = (
            (
                "role_instance",
                "active_attempt_id",
                "dispatch_id",
                "lease_id",
                "provider_session_id",
            )
            if str(row.get("task_pipeline_stage") or "")
            else ("dispatch_id", "provider_session_id")
        )
        for key in reset_keys:
            row[key] = ""
        row["reason"] = str(payload.get("reason") or "")
        row["compatibility_proof_digest"] = str(
            payload.get("compatibility_proof_digest") or ""
        )
    return True


def task_pipeline_request_hash_body(
    request_body: Mapping[str, Any],
    request: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not str(request.get("task_pipeline_stage") or "").strip():
        return request_body
    semantic_request = dict(request)
    for key in (
        "role_instance",
        "active_attempt_id",
        "lease_id",
        "placement_epoch",
        "task_stage_session_binding",
        "attempt_source_manifest_ref",
        "attempt_source_manifest_digest",
        "attempt_source_manifest",
        "source_manifest_digest",
        "input_consumption_policy_ref",
        "input_consumption_policy_digest",
        "input_consumption_policy",
        "read_policy_digest",
    ):
        semantic_request.pop(key, None)
    result_identity = semantic_request.get("result_identity")
    if isinstance(result_identity, Mapping):
        semantic_identity = dict(result_identity)
        for key in (
            "role_instance",
            "active_attempt_id",
            "attempt_id",
            "lease_id",
            "placement_epoch",
            "task_stage_session_binding",
        ):
            semantic_identity.pop(key, None)
        semantic_request["result_identity"] = semantic_identity
    execution_profile = semantic_request.get("execution_profile")
    if isinstance(execution_profile, Mapping):
        semantic_execution_profile = dict(execution_profile)
        semantic_execution_profile.pop("role", None)
        semantic_request["execution_profile"] = semantic_execution_profile
    from zf.runtime.workflow_operation import canonicalize_operation_request

    return {
        **dict(request_body),
        "role_instance": "",
        "active_attempt_id": "",
        "lease_id": "",
        "request": canonicalize_operation_request(semantic_request),
    }


def find_task_pipeline_budget_amendment(
    events: list[ZfEvent],
    operation: Mapping[str, Any],
) -> ZfEvent | None:
    """Find a later owner amendment that authorizes one budget redrive."""

    operation_id = str(operation.get("operation_id") or "").strip()
    workflow_run_id = str(operation.get("workflow_run_id") or "").strip()
    blocked_event_id = str(operation.get("last_event_id") or "").strip()
    if (
        str(operation.get("status") or "") != "blocked"
        or not str(operation.get("task_pipeline_stage") or "").strip()
        or not str(operation.get("reason") or "").startswith(
            "workflow_budget_exceeded:"
        )
        or not operation_id
        or not workflow_run_id
        or not blocked_event_id
    ):
        return None

    blocked_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.id == blocked_event_id
            and event.type == "workflow.operation.blocked"
            and str((event.payload or {}).get("operation_id") or "")
            == operation_id
        ),
        -1,
    )
    if blocked_index < 0:
        return None
    blocked_event = events[blocked_index]
    budget_event = next(
        (
            event
            for event in events[:blocked_index]
            if event.id == str(blocked_event.causation_id or "")
            and event.type == "workflow.budget.exceeded"
            and str((event.payload or {}).get("scope") or "") == "operation"
            and str((event.payload or {}).get("scope_id") or "")
            == operation_id
        ),
        None,
    )
    if budget_event is None:
        return None

    from zf.runtime.run_scope import event_run_id, run_aliases

    aliases = run_aliases(events)
    canonical_run_id = aliases.get(workflow_run_id, workflow_run_id)
    budget_payload = (
        budget_event.payload if isinstance(budget_event.payload, Mapping) else {}
    )
    candidate: ZfEvent | None = None
    for event in events[blocked_index + 1:]:
        if event_run_id(event, aliases=aliases) != canonical_run_id:
            continue
        if event.type in {
            "run.goal.blocked",
            "run.goal.completed",
            "run.completed",
            "run.failed",
        }:
            candidate = None
            continue
        if event.type != "run.goal.updated":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if str(payload.get("status") or "").strip() not in {
            "active",
            "running",
        }:
            candidate = None
            continue
        if str(payload.get("source") or "") != "zf_goal_cli":
            continue
        patch = payload.get("run_limits_patch")
        if "usage_samples" in (budget_payload.get("exceeded_dimensions") or []):
            if str(payload.get("operation_budget_id") or "") != operation_id:
                continue
            patch = payload.get("operation_limits_patch")
        if not isinstance(patch, Mapping):
            continue
        if _budget_patch_covers_exceeded_dimensions(patch, budget_payload):
            candidate = event
    return candidate


def _budget_patch_covers_exceeded_dimensions(
    patch: Mapping[str, Any],
    budget_payload: Mapping[str, Any],
) -> bool:
    exceeded = budget_payload.get("exceeded_dimensions")
    dimensions = [
        str(item or "").strip()
        for item in exceeded or []
        if str(item or "").strip()
    ] if isinstance(exceeded, list) else []
    measurement = budget_payload.get("measurement")
    measured = measurement if isinstance(measurement, Mapping) else {}
    if not dimensions:
        return False
    for dimension in dimensions:
        keys = _BUDGET_AMENDMENT_DIMENSIONS.get(dimension)
        if keys is None:
            return False
        limit_key, measurement_key = keys
        if limit_key not in patch:
            return False
        try:
            amended_limit = float(patch[limit_key])
            current_usage = float(measured.get(measurement_key) or 0)
        except (TypeError, ValueError):
            return False
        if amended_limit < 0:
            return False
        if dimension == "usage_samples" and not amended_limit.is_integer():
            return False
        if amended_limit != 0 and amended_limit <= current_usage:
            return False
    return True


def admit_task_pipeline_redrive(
    service: Any,
    *,
    operation_id: str,
    request_hash: str,
    workflow_run_id: str,
    task_id: str,
    source_attempt_id: str,
    recovery_decision_event_id: str,
    reason: str,
    recovery_decision_owner: str = "run_manager",
    compatibility_proof_digest: str = "",
    compatibility_request_ref: Mapping[str, Any] | None = None,
) -> ZfEvent | None:
    from zf.runtime.workflow_operation import (
        WORKFLOW_OPERATION_SCHEMA,
        WorkflowOperationError,
        load_workflow_operation,
    )

    operation = load_workflow_operation(service.event_log, operation_id)
    if operation is None:
        raise WorkflowOperationError("redrive operation does not exist")
    if str(operation.get("request_hash") or "") != request_hash:
        raise WorkflowOperationError("redrive request hash diverged")
    decision_owner = str(recovery_decision_owner or "").strip()
    if decision_owner not in {
        "run_manager",
        "kernel_replay",
        "controlled_action",
        "operator_budget_amendment",
    }:
        raise WorkflowOperationError("redrive decision owner is invalid")
    operation_status = str(operation.get("status") or "")
    events = service.event_log.read_all()
    legacy_cancelled_restart = (
        operation_status == "cancelled"
        and decision_owner == "kernel_replay"
        and str(operation.get("reason") or "")
        == "workflow_resume_cancelled_interrupted_operation"
    )
    blocked_compatibility_replay = (
        operation_status == "blocked"
        and decision_owner == "kernel_replay"
        and str(operation.get("reason") or "") in {
            "request_hash_divergence",
            "request_hash_compatibility_failed",
        }
        and bool(str(compatibility_proof_digest or ""))
    )
    recovery_decision = next(
        (
            event
            for event in events
            if event.id == recovery_decision_event_id
        ),
        None,
    )
    recovery_payload = (
        recovery_decision.payload
        if recovery_decision is not None
        and isinstance(recovery_decision.payload, dict)
        else {}
    )
    blocked_controlled_integration_retry = (
        operation_status == "blocked"
        and decision_owner == "controlled_action"
        and str(operation.get("task_pipeline_stage") or "") == "integration"
        and str(operation.get("reason") or "").startswith(
            "candidate_incremental_failed:"
        )
        and recovery_decision is not None
        and recovery_decision.type == "repair.action.requested"
        and str(recovery_payload.get("kind") or "")
        == "retry_integration_queue_entry"
        and str(recovery_payload.get("queue_entry_id") or "") == operation_id
        and str(
            recovery_payload.get("task_id")
            or recovery_decision.task_id
            or ""
        )
        == str(operation.get("task_id") or task_id or "")
    )
    budget_amendment = find_task_pipeline_budget_amendment(events, operation)
    blocked_budget_amendment_replay = (
        operation_status == "blocked"
        and decision_owner == "operator_budget_amendment"
        and budget_amendment is not None
        and budget_amendment.id == recovery_decision_event_id
    )
    if (
        operation_status not in {"suspended", "requested"}
        and not legacy_cancelled_restart
        and not blocked_compatibility_replay
        and not blocked_controlled_integration_retry
        and not blocked_budget_amendment_replay
    ):
        raise WorkflowOperationError("redrive requires a suspended operation")
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "workflow.operation.redrive_admitted"
            and str(payload.get("operation_id") or "") == operation_id
            and (
                (
                    compatibility_proof_digest
                    and str(payload.get("compatibility_proof_digest") or "")
                    == compatibility_proof_digest
                    and str(payload.get("recovery_decision_event_id") or "")
                    == recovery_decision_event_id
                )
                or (
                    not compatibility_proof_digest
                    and decision_owner == "kernel_replay"
                    and str(payload.get("recovery_decision_owner") or "")
                    == "kernel_replay"
                    and str(payload.get("recovery_decision_event_id") or "")
                    == recovery_decision_event_id
                )
                or (
                    not compatibility_proof_digest
                    and decision_owner == "operator_budget_amendment"
                    and str(payload.get("recovery_decision_event_id") or "")
                    == recovery_decision_event_id
                )
                or (
                    not compatibility_proof_digest
                    and decision_owner not in {
                        "kernel_replay",
                        "operator_budget_amendment",
                    }
                    and str(payload.get("source_attempt_id") or "")
                    == source_attempt_id
                )
            )
        ):
            return None
    return service.event_writer.append(ZfEvent(
        type="workflow.operation.redrive_admitted",
        actor="zf-cli",
        origin="kernel",
        task_id=task_id or None,
        payload={
            "schema_version": WORKFLOW_OPERATION_SCHEMA,
            "workflow_run_id": workflow_run_id,
            "operation_id": operation_id,
            "request_hash": request_hash,
            "task_id": task_id,
            "source_attempt_id": source_attempt_id,
            "recovery_decision_event_id": recovery_decision_event_id,
            "recovery_decision_owner": decision_owner,
            "recovery_effect_owner": "workflow_runtime_coordinator",
            "recovery_from_status": operation_status,
            "reason": reason,
            "compatibility_proof_digest": compatibility_proof_digest,
            "compatibility_request_ref": dict(compatibility_request_ref or {}),
        },
        causation_id=recovery_decision_event_id or None,
        correlation_id=workflow_run_id or None,
    ))


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "admit_task_pipeline_redrive",
    "apply_call_result_admission",
    "apply_task_pipeline_operation_event",
    "find_task_pipeline_budget_amendment",
    "task_pipeline_operation_seed",
    "task_pipeline_request_fields",
    "task_pipeline_request_hash_body",
]
