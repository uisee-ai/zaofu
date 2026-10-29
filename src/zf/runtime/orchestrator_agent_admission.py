"""Mechanical currentness admission for typed Orchestrator Agent decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_contracts import (
    OrchestratorAgentContractError,
    normalize_orchestration_decision,
)
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


@dataclass(frozen=True)
class OrchestratorAgentAdmission:
    admitted: bool
    reason: str
    operation_id: str = ""
    checkpoint: str = ""
    checkpoint_policy: str = ""
    source_event_id: str = ""
    decision: dict[str, Any] | None = None
    decision_ref: dict[str, Any] | None = None


def admit_orchestrator_agent_decision(
    runtime: Any,
    event: ZfEvent,
) -> OrchestratorAgentAdmission:
    payload = event.payload if isinstance(event.payload, dict) else {}
    operation_id = str(payload.get("operation_id") or "")
    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is None:
        return _reject("operation_missing", operation_id=operation_id)
    if str(operation.get("status") or "") != "settled":
        return _reject("operation_not_settled", operation_id=operation_id)
    request_ref = operation.get("request_ref")
    if not isinstance(request_ref, Mapping):
        return _reject("operation_request_missing", operation_id=operation_id)
    stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
    request = stored.get("request") if isinstance(stored, Mapping) else None
    if not isinstance(request, Mapping):
        return _reject("operation_request_invalid", operation_id=operation_id)
    decision_raw = payload.get("orchestration_decision")
    try:
        decision = normalize_orchestration_decision(decision_raw)
    except OrchestratorAgentContractError as exc:
        return _reject(
            f"decision_schema_invalid:{exc}",
            operation_id=operation_id,
        )
    identity = decision["identity"]
    expected = {
        "operation_id": operation_id,
        "workflow_run_id": str(operation.get("workflow_run_id") or ""),
        "checkpoint": str(request.get("checkpoint") or ""),
        "input_digest": str(request.get("checkpoint_input_digest") or ""),
        "effective_config_digest": str(
            request.get("effective_config_digest") or ""
        ),
        "plan_artifact_package_ref": str(
            request.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            request.get("plan_artifact_package_digest") or ""
        ),
        "task_map_generation": str(
            request.get("task_map_generation") or ""
        ),
    }
    for key, value in expected.items():
        if value and str(identity.get(key) or "") != value:
            return _reject(
                f"identity_mismatch:{key}",
                operation_id=operation_id,
            )
    if expected["checkpoint"] in {"run_start", "pre_impl"} and decision.get("decision") == "adopt":
        from zf.runtime.orchestrator_agent_run_plan import (
            validate_run_plan_admission,
        )

        try:
            validate_run_plan_admission(
                runtime,
                run_plan=decision.get("run_plan") or {},
                request=request,
            )
        except OrchestratorAgentContractError as exc:
            return _reject(
                f"run_plan_invalid:{exc}",
                operation_id=operation_id,
            )
    if expected["checkpoint"] in {"stage_barrier", "pre_closeout"}:
        from zf.runtime.orchestrator_agent_aggregation import (
            validate_aggregation_admission,
        )

        try:
            validate_aggregation_admission(
                runtime,
                aggregation_result=decision.get("aggregation_result") or {},
                request=request,
            )
        except OrchestratorAgentContractError as exc:
            return _reject(
                f"aggregation_result_invalid:{exc}",
                operation_id=operation_id,
            )
    decision_ref = payload.get("control_result_ref")
    settled_ref = operation.get("admitted_call_result_ref")
    envelope_ref = payload.get("call_result_envelope_ref")
    if not isinstance(decision_ref, Mapping) or not isinstance(envelope_ref, Mapping):
        return _reject("admitted_result_refs_missing", operation_id=operation_id)
    if not isinstance(settled_ref, Mapping) or _descriptor(settled_ref) != _descriptor(
        envelope_ref
    ):
        return _reject("settled_result_ref_mismatch", operation_id=operation_id)
    workflow_run_id = expected["workflow_run_id"]
    package_digest = expected["plan_artifact_package_digest"]
    if package_digest:
        current = reduce_plan_artifact_packages(
            runtime.event_log.read_all(),
            workflow_run_id=workflow_run_id,
        ).get("current")
        current = current if isinstance(current, Mapping) else {}
        if (
            str(current.get("package_ref") or "")
            != expected["plan_artifact_package_ref"]
            or str(current.get("package_digest") or "") != package_digest
            or str(current.get("task_map_generation") or "")
            != expected["task_map_generation"]
        ):
            return _reject("plan_package_stale", operation_id=operation_id)
    from zf.runtime.orchestrator_agent_execution_policy import execution_decision_rejection

    try:
        rejection = execution_decision_rejection(runtime, decision=decision, request=request)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rejection = "execution_authority_invalid:" + type(exc).__name__
    if rejection:
        return _reject(rejection, operation_id=operation_id)
    return OrchestratorAgentAdmission(
        admitted=True,
        reason="current_typed_decision",
        operation_id=operation_id,
        checkpoint=expected["checkpoint"],
        checkpoint_policy=str(request.get("checkpoint_policy") or "blocking"),
        source_event_id=str(request.get("source_event_id") or ""),
        decision=decision,
        decision_ref=dict(decision_ref),
    )


def _reject(reason: str, *, operation_id: str) -> OrchestratorAgentAdmission:
    return OrchestratorAgentAdmission(
        admitted=False,
        reason=reason,
        operation_id=operation_id,
    )


def _descriptor(value: Mapping[str, Any]) -> tuple[str, str]:
    return str(value.get("ref") or ""), str(value.get("sha256") or "")


__all__ = ["OrchestratorAgentAdmission", "admit_orchestrator_agent_decision"]
