"""Durable Orchestrator Agent semantic checkpoint operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_capability import (
    bind_attempt_artifact_read_capability,
)
from zf.runtime.orchestrator_agent_context import (
    OrchestratorAgentContext,
    build_orchestrator_agent_context,
)
from zf.runtime.orchestrator_agent_role import orchestrator_instance_id
from zf.runtime.result_submit import bind_operation_submit_capability
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
    reduce_workflow_operations,
    stable_operation_id,
)


CHECKPOINT_REQUESTED = "orchestrator.semantic.checkpoint.requested"
DECISION_SUBMITTED = "orchestrator.semantic.decision.submitted"
DECISION_FAILED = "orchestrator.semantic.decision.failed"
OUTPUT_PROFILE_ID = "orchestrator-semantic-decision"
OUTPUT_PROFILE_REVISION = "1"
OWNER_NARRATIVE_SUBMITTED = "owner.delivery.narrative.submitted"
OWNER_NARRATIVE_FAILED = "owner.delivery.narrative.failed"
OWNER_NARRATIVE_PROFILE_ID = "owner-delivery-narrative"


@dataclass(frozen=True)
class PreparedOrchestratorAgentOperation:
    workflow_run_id: str
    operation_id: str
    request_hash: str
    attempt_id: str
    checkpoint: str
    checkpoint_policy: str
    role_instance: str
    result_scratch_ref: str
    context: OrchestratorAgentContext | None
    should_dispatch: bool
    status: str
    replay_hit: bool = False


def request_orchestrator_agent_checkpoint(
    runtime: Any,
    *,
    checkpoint: str,
    checkpoint_policy: str,
    workflow_run_id: str,
    source_event: ZfEvent,
    payload: Mapping[str, Any],
) -> PreparedOrchestratorAgentOperation:
    role_instance = orchestrator_instance_id(runtime)
    checkpoint_source_event_id = canonical_checkpoint_source_event_id(
        source_event,
        payload,
    )
    revision = orchestrator_agent_checkpoint_revision(
        checkpoint=checkpoint,
        source_event_id=checkpoint_source_event_id,
        payload=payload,
    )
    operation_id = orchestrator_agent_checkpoint_operation_id(
        checkpoint=checkpoint,
        workflow_run_id=workflow_run_id,
        source_event_id=checkpoint_source_event_id,
        payload=payload,
    )
    candidate_key = orchestrator_agent_checkpoint_candidate_key(
        checkpoint=checkpoint,
        source_event_id=checkpoint_source_event_id,
        payload=payload,
    )
    attempt_id = f"oa-{operation_id}"
    if checkpoint_policy == "shadow":
        from zf.runtime.orchestrator_agent_policy import (
            shadow_checkpoint_selection,
        )

        selection = shadow_checkpoint_selection(
            runtime.config,
            checkpoint,
            workflow_run_id=workflow_run_id,
            revision=revision,
            flow_kind=str(payload.get("flow_kind") or ""),
            payload=payload,
        )
        if not selection.selected:
            _record_shadow_checkpoint_skip(
                runtime,
                operation_id=operation_id,
                workflow_run_id=workflow_run_id,
                checkpoint=checkpoint,
                source_event=source_event,
                source_event_id=checkpoint_source_event_id,
                flow_kind=str(payload.get("flow_kind") or ""),
                selection=selection,
            )
            return PreparedOrchestratorAgentOperation(
                workflow_run_id=workflow_run_id,
                operation_id=operation_id,
                request_hash="",
                attempt_id=attempt_id,
                checkpoint=checkpoint,
                checkpoint_policy=checkpoint_policy,
                role_instance=role_instance,
                result_scratch_ref="",
                context=None,
                should_dispatch=False,
                status="skipped",
            )
    existing = load_workflow_operation(runtime.event_log, operation_id)
    if existing is not None:
        prepared = _prepared_operation_from_stored_operation(
            runtime,
            operation_id=operation_id,
            operation=existing,
        )
        if prepared is not None:
            return replace(
                prepared,
                should_dispatch=prepared.status == "requested",
                replay_hit=True,
            )
    context = build_orchestrator_agent_context(
        runtime,
        checkpoint=checkpoint,
        checkpoint_policy=checkpoint_policy,
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        attempt_id=attempt_id,
        source_event_id=checkpoint_source_event_id,
        payload=payload,
    )
    identity = context.input_body["identity"]
    result_scratch_ref = (
        Path("tmp") / "result-submit" / operation_id / attempt_id / "result.json"
    ).as_posix()
    owner_delivery = checkpoint == "owner_delivery"
    output_profile_id = (
        OWNER_NARRATIVE_PROFILE_ID if owner_delivery else OUTPUT_PROFILE_ID
    )
    success_event = (
        OWNER_NARRATIVE_SUBMITTED if owner_delivery else DECISION_SUBMITTED
    )
    failure_event = OWNER_NARRATIVE_FAILED if owner_delivery else DECISION_FAILED
    result_identity = {
        "operation_id": operation_id,
        "workflow_run_id": workflow_run_id,
        "checkpoint": checkpoint,
        "checkpoint_policy": checkpoint_policy,
        "checkpoint_input_digest": str(context.input_ref["sha256"]),
        "effective_config_digest": str(context.effective_config_ref["sha256"]),
        "plan_revision": str(identity.get("plan_revision") or ""),
        "task_map_generation": str(identity.get("task_map_generation") or ""),
        "plan_artifact_package_id": str(
            identity.get("plan_artifact_package_id") or ""
        ),
        "plan_artifact_package_ref": str(
            identity.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            identity.get("plan_artifact_package_digest") or ""
        ),
        "goal_id": str(identity.get("goal_id") or ""),
        "run_contract_ref": str(identity.get("run_contract_ref") or ""),
        "run_contract_digest": str(
            identity.get("run_contract_digest") or ""
        ),
        "source_event_id": checkpoint_source_event_id,
        "checkpoint_candidate_key": candidate_key,
    }
    if owner_delivery:
        result_identity.update({
            key: str(payload.get(key) or "")
            for key in (
                "terminal_event_id",
                "terminal_event_type",
                "dossier_ref",
                "dossier_source_fingerprint",
                "completion_receipt_ref",
                "completion_receipt_fingerprint",
            )
        })
    request = {
        "operation_id": operation_id,
        "workflow_run_id": workflow_run_id,
        "operation_type": "orchestrator_agent_semantic",
        "stage_id": f"oa-{checkpoint}",
        "checkpoint": checkpoint,
        "checkpoint_policy": checkpoint_policy,
        "attempt_domain": "run",
        "role_instance": role_instance,
        "active_attempt_id": attempt_id,
        "lease_id": attempt_id,
        "checkpoint_input_ref": context.input_ref,
        "checkpoint_input_digest": str(context.input_ref["sha256"]),
        "source_manifest_ref": context.source_manifest_ref,
        "source_manifest_digest": str(context.source_manifest_ref["sha256"]),
        "input_consumption_policy_ref": context.read_policy_ref,
        "read_policy_digest": str(context.read_policy_ref["sha256"]),
        "stage_execution_card_ref": context.stage_card_ref,
        "effective_config_ref": context.effective_config_ref,
        "effective_config_digest": str(context.effective_config_ref["sha256"]),
        "plan_revision": str(identity.get("plan_revision") or ""),
        "task_map_generation": str(identity.get("task_map_generation") or ""),
        "plan_artifact_package_id": str(
            identity.get("plan_artifact_package_id") or ""
        ),
        "plan_artifact_package_ref": str(
            identity.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            identity.get("plan_artifact_package_digest") or ""
        ),
        "goal_id": str(identity.get("goal_id") or ""),
        "run_contract_ref": str(identity.get("run_contract_ref") or ""),
        "run_contract_digest": str(
            identity.get("run_contract_digest") or ""
        ),
        "source_event_id": checkpoint_source_event_id,
        "aggregation_input_refs": [
            dict(item)
            for item in payload.get("aggregation_input_refs", [])
            if isinstance(item, Mapping)
        ],
        "output_profile_id": output_profile_id,
        "output_profile_revision": OUTPUT_PROFILE_REVISION,
        "semantic_result_submit_mode": "blocking",
        "canonical_success_event": success_event,
        "canonical_failure_event": failure_event,
        "result_scratch_ref": result_scratch_ref,
        "result_identity": result_identity,
    }
    service = workflow_operation_service(runtime)
    ensured = service.ensure_operation(
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        operation_type="orchestrator_agent_semantic",
        request=request,
        parent_stage_id=f"oa-{checkpoint}",
        role_instance=role_instance,
        active_attempt_id=attempt_id,
        lease_id=attempt_id,
        causation_id=source_event.id,
        correlation_id=source_event.correlation_id or workflow_run_id,
    )
    superseded_operation_ids: list[str] = []
    if ensured.created and candidate_key:
        superseded_operation_ids = _supersede_obsolete_checkpoint_operations(
            runtime,
            service=service,
            workflow_run_id=workflow_run_id,
            checkpoint=checkpoint,
            candidate_key=candidate_key,
            operation_id=operation_id,
            source_event=source_event,
        )
    if ensured.created:
        bind_attempt_artifact_read_capability(
            runtime.state_dir,
            operation_id=operation_id,
            attempt_id=attempt_id,
            role_instance=role_instance,
            manifest=context.source_manifest,
        )
        bind_operation_submit_capability(
            runtime.state_dir,
            operation_id=operation_id,
            role_instance=role_instance,
            attempt_id=attempt_id,
            lease_id=attempt_id,
        )
    if ensured.created:
        runtime.event_writer.append(ZfEvent(
            type=CHECKPOINT_REQUESTED,
            actor="zf-cli",
            origin="kernel",
            payload={
                "schema_version": "orchestrator-agent-checkpoint-request.v1",
                "workflow_run_id": workflow_run_id,
                "operation_id": operation_id,
                "request_hash": ensured.request_hash,
                "attempt_id": attempt_id,
                "checkpoint": checkpoint,
                "checkpoint_policy": checkpoint_policy,
                "checkpoint_input_ref": context.input_ref,
                "source_manifest_ref": context.source_manifest_ref,
                "input_consumption_policy_ref": context.read_policy_ref,
                "stage_execution_card_ref": context.stage_card_ref,
                "source_event_id": checkpoint_source_event_id,
                "checkpoint_candidate_key": candidate_key,
                "supersedes_operation_ids": superseded_operation_ids,
            },
            causation_id=source_event.id,
            correlation_id=source_event.correlation_id or workflow_run_id,
        ))
    return PreparedOrchestratorAgentOperation(
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        attempt_id=attempt_id,
        checkpoint=checkpoint,
        checkpoint_policy=checkpoint_policy,
        role_instance=role_instance,
        result_scratch_ref=result_scratch_ref,
        context=context,
        should_dispatch=ensured.status == "requested",
        status=ensured.status,
        replay_hit=ensured.replay_hit,
    )


def _record_shadow_checkpoint_skip(
    runtime: Any,
    *,
    operation_id: str,
    workflow_run_id: str,
    checkpoint: str,
    source_event: ZfEvent,
    source_event_id: str,
    flow_kind: str,
    selection: Any,
) -> None:
    if any(
        event.type == "orchestrator.semantic.checkpoint.skipped"
        and isinstance(event.payload, Mapping)
        and str(event.payload.get("operation_id") or "") == operation_id
        for event in runtime.event_log.read_all()
    ):
        return
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.checkpoint.skipped",
        actor="zf-cli",
        origin="kernel",
        payload={
            "schema_version": "orchestrator-semantic-checkpoint-skip.v1",
            "operation_id": operation_id,
            "workflow_run_id": workflow_run_id,
            "checkpoint": checkpoint,
            "checkpoint_policy": "shadow",
            "flow_kind": flow_kind,
            "source_event_id": source_event_id,
            "sample_percent": selection.sample_percent,
            "sample_bucket": selection.bucket,
            "reason": selection.reason,
            "risk_signals": list(selection.risk_signals),
        },
        causation_id=source_event.id,
        correlation_id=source_event.correlation_id or workflow_run_id,
    ))


def canonical_checkpoint_source_event_id(
    source_event: ZfEvent,
    payload: Mapping[str, Any],
) -> str:
    """Bind replay to the original business trigger, not its redispatch."""

    return str(
        payload.get("original_trigger_event_id")
        or payload.get("checkpoint_source_event_id")
        or source_event.id
    )


def orchestrator_agent_checkpoint_revision(
    *,
    checkpoint: str,
    source_event_id: str,
    payload: Mapping[str, Any],
) -> str:
    """Return immutable semantic identity for one OA checkpoint candidate."""

    revision = str(
        payload.get("plan_revision")
        or payload.get("task_map_generation")
        or payload.get("request_revision")
        or "1"
    )
    if checkpoint == "plan_candidate":
        candidate_key = orchestrator_agent_checkpoint_candidate_key(
            checkpoint=checkpoint,
            source_event_id=source_event_id,
            payload=payload,
        )
        if candidate_key:
            return candidate_key
        return ":".join((revision, source_event_id))
    if checkpoint == "pre_impl":
        return str(
            payload.get("task_map_generation")
            or payload.get("plan_artifact_package_digest")
            or revision
        )
    if checkpoint in {"stage_barrier", "goal_revision", "pre_closeout"}:
        return ":".join((revision, source_event_id))
    if checkpoint == "semantic_failure":
        return ":".join((
            revision,
            str(payload.get("failure_fingerprint") or source_event_id),
        ))
    if checkpoint == "owner_delivery":
        return ":".join((
            revision,
            str(payload.get("dossier_source_fingerprint") or source_event_id),
        ))
    return revision


def orchestrator_agent_checkpoint_candidate_key(
    *,
    checkpoint: str,
    source_event_id: str,
    payload: Mapping[str, Any],
) -> str:
    """Return current-candidate identity independent of display revision."""

    if checkpoint != "plan_candidate":
        return ""
    generation = str(payload.get("task_map_generation") or "").strip()
    candidate_digest = str(
        payload.get("plan_artifact_package_digest")
        or payload.get("task_map_digest")
        or ""
    ).strip()
    if candidate_digest:
        return f"generation:{generation or 'unknown'}:digest:{candidate_digest}"
    candidate_ref = str(
        payload.get("plan_artifact_package_id")
        or payload.get("plan_artifact_package_ref")
        or payload.get("task_map_ref")
        or ""
    ).strip()
    if candidate_ref:
        return f"generation:{generation or 'unknown'}:legacy-ref:{candidate_ref}"
    return ""


def _supersede_obsolete_checkpoint_operations(
    runtime: Any,
    *,
    service: WorkflowOperationService,
    workflow_run_id: str,
    checkpoint: str,
    candidate_key: str,
    operation_id: str,
    source_event: ZfEvent,
) -> list[str]:
    """Fence older in-flight OA candidates after a new candidate is durable."""

    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    superseded: list[str] = []
    operations = reduce_workflow_operations(
        runtime.event_log.read_all(),
        workflow_run_id=workflow_run_id,
    )
    for previous_id, operation in operations.items():
        if previous_id == operation_id:
            continue
        if str(operation.get("parent_stage_id") or "") != f"oa-{checkpoint}":
            continue
        if str(operation.get("status") or "") not in {
            "requested",
            "reserved",
            "running",
            "suspended",
        }:
            continue
        request_ref = operation.get("request_ref")
        if not isinstance(request_ref, Mapping):
            continue
        try:
            stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
        except (OSError, ValueError, TypeError):
            continue
        request = stored.get("request") if isinstance(stored, Mapping) else None
        if not isinstance(request, Mapping):
            continue
        previous_key = str(request.get("checkpoint_candidate_key") or "")
        if not previous_key:
            previous_key = orchestrator_agent_checkpoint_candidate_key(
                checkpoint=checkpoint,
                source_event_id=str(request.get("source_event_id") or ""),
                payload=request,
            )
        if not previous_key or previous_key == candidate_key:
            continue
        service.supersede(
            operation_id=previous_id,
            request_hash=str(operation.get("request_hash") or ""),
            workflow_run_id=workflow_run_id,
            reason=f"checkpoint_candidate_superseded_by:{operation_id}",
            causation_id=source_event.id,
            correlation_id=source_event.correlation_id or workflow_run_id,
        )
        superseded.append(previous_id)
    return superseded


def orchestrator_agent_checkpoint_operation_id(
    *,
    checkpoint: str,
    workflow_run_id: str,
    source_event_id: str,
    payload: Mapping[str, Any],
) -> str:
    revision = orchestrator_agent_checkpoint_revision(
        checkpoint=checkpoint,
        source_event_id=source_event_id,
        payload=payload,
    )
    return stable_operation_id(
        workflow_run_id=workflow_run_id,
        parent_stage_id=f"orchestrator-agent:{checkpoint}",
        operation_key=f"checkpoint@revision:{revision}",
        operation_type="orchestrator_agent_semantic",
    )


def activate_orchestrator_agent_operation(
    runtime: Any,
    prepared: PreparedOrchestratorAgentOperation,
    *,
    dispatch_id: str,
    causation_id: str,
) -> None:
    workflow_operation_service(runtime).mark_started(
        operation_id=prepared.operation_id,
        request_hash=prepared.request_hash,
        workflow_run_id=prepared.workflow_run_id,
        dispatch_id=dispatch_id,
        role_instance=prepared.role_instance,
        active_attempt_id=prepared.attempt_id,
        lease_id=prepared.attempt_id,
        causation_id=causation_id,
        correlation_id=prepared.workflow_run_id,
    )


def retry_orchestrator_agent_operation(
    runtime: Any,
    prepared: PreparedOrchestratorAgentOperation,
    *,
    retry_attempt: int,
    dispatch_id: str,
    causation_id: str,
) -> None:
    workflow_operation_service(runtime).mark_retry_started(
        operation_id=prepared.operation_id,
        request_hash=prepared.request_hash,
        workflow_run_id=prepared.workflow_run_id,
        retry_attempt=retry_attempt,
        reason="retry_after_orchestrator_pane_respawn",
        dispatch_id=dispatch_id,
        role_instance=prepared.role_instance,
        active_attempt_id=prepared.attempt_id,
        lease_id=prepared.attempt_id,
        causation_id=causation_id,
        correlation_id=prepared.workflow_run_id,
    )


def interrupt_orchestrator_agent_operation(
    runtime: Any,
    prepared: PreparedOrchestratorAgentOperation,
    *,
    reason: str,
    causation_id: str,
) -> None:
    workflow_operation_service(runtime).interrupt(
        operation_id=prepared.operation_id,
        request_hash=prepared.request_hash,
        workflow_run_id=prepared.workflow_run_id,
        reason=reason,
        causation_id=causation_id,
        correlation_id=prepared.workflow_run_id,
    )


def fail_orchestrator_agent_operation(
    runtime: Any,
    prepared: PreparedOrchestratorAgentOperation,
    *,
    reason: str,
    causation_id: str,
) -> None:
    workflow_operation_service(runtime).fail(
        operation_id=prepared.operation_id,
        request_hash=prepared.request_hash,
        workflow_run_id=prepared.workflow_run_id,
        reason=reason,
        causation_id=causation_id,
        correlation_id=prepared.workflow_run_id,
    )


def prepared_operation_from_checkpoint_event(
    runtime: Any,
    event: ZfEvent,
) -> PreparedOrchestratorAgentOperation | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    operation_id = str(payload.get("operation_id") or "")
    operation = load_workflow_operation(runtime.event_log, operation_id)
    retry_attempt = int(payload.get("checkpoint_dispatch_retry_attempt") or 0)
    status = str((operation or {}).get("status") or "")
    retryable_status = (
        retry_attempt == 1
        and status in {"suspended", "failed"}
        and (
            status == "suspended"
            or "pane_dead" in str((operation or {}).get("reason") or "")
        )
    )
    if operation is None or (status != "requested" and not retryable_status):
        return None
    return _prepared_operation_from_stored_operation(
        runtime,
        operation_id=operation_id,
        operation=operation,
        should_dispatch=True,
    )


def _prepared_operation_from_stored_operation(
    runtime: Any,
    *,
    operation_id: str,
    operation: Mapping[str, Any],
    should_dispatch: bool | None = None,
) -> PreparedOrchestratorAgentOperation | None:
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    status = str(operation.get("status") or "")
    request_ref = operation.get("request_ref")
    if not isinstance(request_ref, Mapping):
        return None
    stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
    request = stored.get("request") if isinstance(stored, Mapping) else None
    if not isinstance(request, Mapping):
        return None
    manifest_ref = request.get("source_manifest_ref")
    policy_ref = request.get("input_consumption_policy_ref")
    input_ref = request.get("checkpoint_input_ref")
    card_ref = request.get("stage_execution_card_ref")
    config_ref = request.get("effective_config_ref")
    if not all(
        isinstance(value, Mapping)
        for value in (manifest_ref, policy_ref, input_ref, card_ref, config_ref)
    ):
        return None
    manifest = hydrate_sidecar_ref(runtime.state_dir, dict(manifest_ref)).payload
    policy = hydrate_sidecar_ref(runtime.state_dir, dict(policy_ref)).payload
    input_body = hydrate_sidecar_ref(runtime.state_dir, dict(input_ref)).payload
    card = hydrate_sidecar_ref(runtime.state_dir, dict(card_ref)).payload
    if not all(isinstance(value, dict) for value in (manifest, policy, input_body, card)):
        return None
    context = OrchestratorAgentContext(
        input_body=dict(input_body),
        input_ref=dict(input_ref),
        source_manifest=dict(manifest),
        source_manifest_ref=dict(manifest_ref),
        read_policy=dict(policy),
        read_policy_ref=dict(policy_ref),
        stage_card=dict(card),
        stage_card_ref=dict(card_ref),
        effective_config_ref=dict(config_ref),
    )
    return PreparedOrchestratorAgentOperation(
        workflow_run_id=str(operation.get("workflow_run_id") or ""),
        operation_id=operation_id,
        request_hash=str(operation.get("request_hash") or ""),
        attempt_id=str(operation.get("active_attempt_id") or ""),
        checkpoint=str(request.get("checkpoint") or ""),
        checkpoint_policy=str(request.get("checkpoint_policy") or "blocking"),
        role_instance=str(operation.get("role_instance") or "orchestrator"),
        result_scratch_ref=str(request.get("result_scratch_ref") or ""),
        context=context,
        should_dispatch=(
            status == "requested"
            if should_dispatch is None
            else should_dispatch
        ),
        status=status,
    )


def workflow_operation_service(runtime: Any) -> WorkflowOperationService:
    service = getattr(runtime, "_oa_workflow_operation_service_v1", None)
    if service is None:
        service = WorkflowOperationService(
            state_dir=runtime.state_dir,
            event_log=runtime.event_log,
            event_writer=runtime.event_writer,
        )
        runtime._oa_workflow_operation_service_v1 = service
    return service


__all__ = [
    "CHECKPOINT_REQUESTED",
    "DECISION_FAILED",
    "DECISION_SUBMITTED",
    "OUTPUT_PROFILE_ID",
    "OUTPUT_PROFILE_REVISION",
    "OWNER_NARRATIVE_FAILED",
    "OWNER_NARRATIVE_PROFILE_ID",
    "OWNER_NARRATIVE_SUBMITTED",
    "PreparedOrchestratorAgentOperation",
    "activate_orchestrator_agent_operation",
    "canonical_checkpoint_source_event_id",
    "fail_orchestrator_agent_operation",
    "interrupt_orchestrator_agent_operation",
    "orchestrator_agent_checkpoint_candidate_key",
    "orchestrator_agent_checkpoint_operation_id",
    "orchestrator_agent_checkpoint_revision",
    "prepared_operation_from_checkpoint_event",
    "request_orchestrator_agent_checkpoint",
    "retry_orchestrator_agent_operation",
]
