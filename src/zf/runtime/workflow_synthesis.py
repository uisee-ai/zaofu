"""Typed Workflow Synthesis caller and deterministic result admission."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.events import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_request_routing import synthesis_request_is_ready
from zf.runtime.workflow_synthesis_support import (
    ALLOWED_FLOW_FAMILIES,
    WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
    admission_catalog as _admission_catalog,
    admission_contract,
    normalized_strings as _strings,
    safe_component as _safe_component,
    synthesis_prompt as _synthesis_prompt,
)
from zf.runtime.workflow_synthesis_generic import (
    FLOW_PARAMETER_KEYS as _FLOW_PARAMETER_KEYS,
    GenericWorkflowSynthesisError,
    admit_generic_workflow_selection,
    canonical_flow_family as _canonical_flow_family,
)
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
    stable_operation_id,
)
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    bind_workflow_synthesis_operation,
    bind_workflow_synthesis_result,
    hydrate_workflow_requirement,
    load_workflow_request,
)


WORKFLOW_SYNTHESIS_OPERATION_TYPE = "workflow_synthesis"
WORKFLOW_SYNTHESIS_PROPOSAL_RETRY_LIMIT = 3
_RESULT_KEYS = frozenset({
    "schema_version",
    "request_id",
    "request_revision",
    "requirement_ref",
    "requirement_digest",
    "selected_flow_family",
    "short_flow_spec",
    "decision_rationale",
    "assumptions",
    "open_questions",
    "requested_roles",
    "requested_skills",
    "requested_profiles",
    "completion_profile",
    "risk_hints",
})
_FLOW_SPEC_KEYS = frozenset({
    "flow_family",
    "intent",
    "parameters",
    "purpose",
    "template",
})
_COMPLETION_KEYS = frozenset({
    "id",
    "delivery_policy",
    "completion_threshold",
    "required_artifacts",
})


class WorkflowSynthesisError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowSynthesisOutcome:
    operation_id: str
    request_hash: str
    result: dict[str, Any]
    result_ref: dict[str, Any]
    request_projection: dict[str, Any]
    replayed: bool = False


@dataclass(frozen=True)
class WorkflowSynthesisQueueOutcome:
    operation_id: str
    request_hash: str
    request_id: str
    status: str
    created: bool = False
    replayed: bool = False


def enqueue_workflow_synthesis(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    writer: EventWriter,
    request_id: str,
    actor: str,
    backend: str,
    operation_context: Mapping[str, Any] | None = None,
    causation_id: str = "",
) -> WorkflowSynthesisQueueOutcome:
    """Persist one synthesis operation without invoking its Provider."""

    prepared = _prepare_synthesis_operation(
        state_dir=state_dir,
        project_root=project_root,
        config=config,
        request_id=request_id,
        actor=actor,
        backend=backend,
        operation_context=operation_context,
    )
    service = WorkflowOperationService(
        state_dir=Path(state_dir),
        event_log=writer.event_log,
        event_writer=writer,
    )
    ensured = service.ensure_operation(
        workflow_run_id=prepared["workflow_run_id"],
        operation_id=prepared["operation_id"],
        operation_type=WORKFLOW_SYNTHESIS_OPERATION_TYPE,
        request=prepared["operation_request"],
        parent_stage_id="workflow-synthesis",
        causation_id=causation_id,
        correlation_id=request_id,
    )
    if ensured.status == "divergent":
        raise WorkflowSynthesisError(
            "workflow synthesis operation request diverged"
        )
    status = {
        "requested": "queued",
        "running": "running",
        "settled": "settled",
        "failed": "failed",
        "blocked": "blocked",
        "superseded": "superseded",
        "cancelled": "cancelled",
    }.get(ensured.status, ensured.status)
    bind_workflow_synthesis_operation(
        state_dir,
        request_id=request_id,
        request_revision=prepared["revision"],
        operation_id=ensured.operation_id,
        request_hash=ensured.request_hash,
        actor=actor,
        writer=writer,
    )
    if ensured.created:
        writer.append(ZfEvent(
            type="workflow.synthesis.queued",
            actor=actor,
            causation_id=causation_id or None,
            correlation_id=request_id,
            payload={
                "request_id": request_id,
                "request_revision": prepared["revision"],
                "operation_id": ensured.operation_id,
                "request_hash": ensured.request_hash,
                "backend": backend,
                "status": "queued",
            },
        ))
    return WorkflowSynthesisQueueOutcome(
        operation_id=ensured.operation_id,
        request_hash=ensured.request_hash,
        request_id=request_id,
        status=status,
        created=ensured.created,
        replayed=ensured.replay_hit,
    )


def run_workflow_synthesis(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    writer: EventWriter,
    request_id: str,
    actor: str,
    backend: str = "",
    candidate_result: Mapping[str, Any] | None = None,
    agent: Any | None = None,
    operation_context: Mapping[str, Any] | None = None,
    causation_id: str = "",
    resume_running: bool = False,
) -> WorkflowSynthesisOutcome:
    """Run or admit one synthesis result through a Workflow Operation."""

    state_dir = Path(state_dir)
    prepared = _prepare_synthesis_operation(
        state_dir=state_dir,
        project_root=project_root,
        config=config,
        request_id=request_id,
        actor=actor,
        backend=backend,
        operation_context=operation_context,
    )
    request = prepared["request"]
    requirement = prepared["requirement"]
    requirement_ref = prepared["requirement_ref"]
    requirement_digest = prepared["requirement_digest"]
    revision = prepared["revision"]
    workflow_run_id = prepared["workflow_run_id"]
    operation_id = prepared["operation_id"]
    catalog = prepared["catalog"]
    operation_request = prepared["operation_request"]
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=writer.event_log,
        event_writer=writer,
    )
    ensured = service.ensure_operation(
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        operation_type=WORKFLOW_SYNTHESIS_OPERATION_TYPE,
        request=operation_request,
        parent_stage_id="workflow-synthesis",
        causation_id=causation_id,
        correlation_id=request_id,
    )
    if ensured.status == "divergent":
        raise WorkflowSynthesisError(
            "workflow synthesis operation request diverged"
        )
    if ensured.status == "settled":
        return _replay_settled_synthesis(
            state_dir=state_dir,
            writer=writer,
            request=request,
            operation_id=operation_id,
            request_hash=ensured.request_hash,
            actor=actor,
        )
    if ensured.status == "running" and resume_running:
        prepared_result = _prepared_synthesis_result(
            state_dir=state_dir,
            writer=writer,
            operation_id=operation_id,
            request=request,
        )
        if prepared_result is not None:
            return _settle_synthesis_result(
                state_dir=state_dir,
                writer=writer,
                service=service,
                request=request,
                admitted=prepared_result[0],
                result_ref=prepared_result[1],
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                workflow_run_id=workflow_run_id,
                revision=revision,
                requirement_digest=requirement_digest,
                actor=actor,
                causation_id=causation_id,
            )
        writer.append(ZfEvent(
            type="workflow.synthesis.retried",
            actor=actor,
            causation_id=causation_id or None,
            correlation_id=request_id,
            payload={
                "request_id": request_id,
                "request_revision": revision,
                "operation_id": operation_id,
                "request_hash": ensured.request_hash,
                "reason": "recover running synthesis operation",
            },
        ))
    elif ensured.status in {
        "running",
        "reserved",
        "failed",
        "blocked",
        "superseded",
        "cancelled",
    }:
        raise WorkflowSynthesisError(
            f"workflow synthesis operation cannot start from {ensured.status}"
        )

    if ensured.status != "running":
        service.mark_started(
            operation_id=operation_id,
            request_hash=ensured.request_hash,
            workflow_run_id=workflow_run_id,
            causation_id=causation_id,
            correlation_id=request_id,
        )
    writer.append(ZfEvent(
        type="workflow.synthesis.requested",
        actor=actor,
        causation_id=causation_id or None,
        correlation_id=request_id,
        payload={
            "request_id": request_id,
            "request_revision": revision,
            "operation_id": operation_id,
            "request_hash": ensured.request_hash,
            "requirement_spec_ref": requirement_ref,
            "requirement_spec_digest": requirement_digest,
            "backend": backend,
        },
    ))
    try:
        raw_result = (
            dict(candidate_result)
            if isinstance(candidate_result, Mapping)
            else _invoke_synthesis_agent(
                state_dir=state_dir,
                project_root=project_root,
                request=request,
                requirement=requirement,
                operation_id=operation_id,
                backend=backend,
                agent=agent,
                catalog=catalog,
            )
        )
        _require_synthesis_operation_open(
            writer,
            operation_id=operation_id,
        )
        admitted = _admit_result(
            raw_result,
            request=request,
            catalog=catalog,
            state_dir=state_dir,
            operation_id=operation_id,
        )
        result_ref = write_immutable_json_sidecar(
            state_dir,
            admitted,
            root=f"workflow/synthesis/{_safe_component(request_id)}/results",
            kind="workflow_synthesis_result",
            schema_version=WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
            created_by="workflow-synthesis-admission",
            source_event_id=causation_id,
        )
        writer.append(ZfEvent(
            type="workflow.synthesis.result.prepared",
            actor=actor,
            causation_id=causation_id,
            correlation_id=request_id,
            payload={
                "request_id": request_id,
                "request_revision": revision,
                "operation_id": operation_id,
                "request_hash": ensured.request_hash,
                "result_ref": result_ref,
            },
        ))
        return _settle_synthesis_result(
            state_dir=state_dir,
            writer=writer,
            service=service,
            request=request,
            admitted=admitted,
            result_ref=result_ref,
            operation_id=operation_id,
            request_hash=ensured.request_hash,
            workflow_run_id=workflow_run_id,
            revision=revision,
            requirement_digest=requirement_digest,
            actor=actor,
            causation_id=causation_id,
        )
    except Exception as exc:
        current = load_workflow_operation(writer.event_log, operation_id) or {}
        current_status = str(current.get("status") or "")
        if current_status not in {
            "settled",
            "cancelled",
            "superseded",
            "blocked",
            "failed",
        }:
            service.fail(
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                workflow_run_id=workflow_run_id,
                reason=str(exc),
                causation_id=causation_id,
                correlation_id=request_id,
            )
        writer.append(ZfEvent(
            type=(
                "workflow.synthesis.result.discarded"
                if current_status in {"cancelled", "superseded"}
                else "workflow.synthesis.failed"
            ),
            actor=actor,
            causation_id=causation_id or None,
            correlation_id=request_id,
            payload={
                "request_id": request_id,
                "request_revision": revision,
                "operation_id": operation_id,
                "reason": str(exc)[:512],
                "operation_status": current_status,
            },
        ))
        if isinstance(exc, WorkflowSynthesisError):
            raise
        raise WorkflowSynthesisError(str(exc)) from exc


def _prepared_synthesis_result(
    *,
    state_dir: Path,
    writer: EventWriter,
    operation_id: str,
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    prepared = next((
        event
        for event in reversed(writer.event_log.read_all())
        if event.type == "workflow.synthesis.result.prepared"
        and str((event.payload or {}).get("operation_id") or "")
        == operation_id
    ), None)
    if prepared is None:
        return None
    descriptor = (prepared.payload or {}).get("result_ref")
    if not isinstance(descriptor, Mapping):
        raise WorkflowSynthesisError(
            "prepared workflow synthesis result has no immutable ref"
        )
    result = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    if not isinstance(result, dict):
        raise WorkflowSynthesisError(
            "prepared workflow synthesis result is unreadable"
        )
    if (
        str(result.get("schema_version") or "")
        != WORKFLOW_SYNTHESIS_RESULT_SCHEMA
        or str(result.get("operation_id") or "") != operation_id
        or str(result.get("request_id") or "")
        != str(request.get("request_id") or "")
        or int(result.get("request_revision") or 0)
        != int(request.get("revision") or 0)
        or str(result.get("requirement_digest") or "")
        != str(request.get("requirement_spec_digest") or "")
        or _canonical_flow_family(result.get("selected_flow_family"))
        not in ALLOWED_FLOW_FAMILIES
    ):
        raise WorkflowSynthesisError(
            "prepared workflow synthesis result identity is invalid"
        )
    return result, dict(descriptor)


def _settle_synthesis_result(
    *,
    state_dir: Path,
    writer: EventWriter,
    service: WorkflowOperationService,
    request: Mapping[str, Any],
    admitted: dict[str, Any],
    result_ref: dict[str, Any],
    operation_id: str,
    request_hash: str,
    workflow_run_id: str,
    revision: int,
    requirement_digest: str,
    actor: str,
    causation_id: str,
) -> WorkflowSynthesisOutcome:
    request_id = str(request.get("request_id") or "")
    _require_synthesis_operation_open(
        writer,
        operation_id=operation_id,
    )
    service.settle(
        operation_id=operation_id,
        request_hash=request_hash,
        workflow_run_id=workflow_run_id,
        admitted_call_result_ref=result_ref,
        causation_id=causation_id,
        correlation_id=request_id,
    )
    settled = load_workflow_operation(writer.event_log, operation_id) or {}
    if str(settled.get("status") or "") != "settled":
        raise WorkflowSynthesisError(
            "workflow synthesis result lost the terminal-state race"
        )
    projection = bind_workflow_synthesis_result(
        state_dir,
        request_id=request_id,
        request_revision=revision,
        requirement_digest=requirement_digest,
        synthesis_ref=result_ref,
        synthesis_digest=str(result_ref.get("sha256") or ""),
        selected_flow_family=str(admitted["selected_flow_family"]),
        open_questions=list(admitted["open_questions"]),
        actor=actor,
        writer=writer,
    )
    return WorkflowSynthesisOutcome(
        operation_id=operation_id,
        request_hash=request_hash,
        result=admitted,
        result_ref=result_ref,
        request_projection=projection,
    )


def _require_synthesis_operation_open(
    writer: EventWriter,
    *,
    operation_id: str,
) -> None:
    current = load_workflow_operation(writer.event_log, operation_id) or {}
    status = str(current.get("status") or "")
    if status in {"cancelled", "superseded", "blocked", "failed"}:
        raise WorkflowSynthesisError(
            f"workflow synthesis operation is terminal: {status}"
        )


def consume_workflow_synthesis_operations(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    writer: EventWriter,
    agent: Any | None = None,
    limit: int = 1,
) -> int:
    from zf.runtime.workflow_synthesis_consumer import (
        consume_workflow_synthesis_operations as consume,
    )

    return consume(
        state_dir=state_dir,
        project_root=project_root,
        config=config,
        writer=writer,
        agent=agent,
        limit=limit,
    )


def _prepare_synthesis_operation(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    request_id: str,
    actor: str,
    backend: str,
    operation_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    request = load_workflow_request(state_dir, request_id)
    if not request:
        raise WorkflowSynthesisError(
            f"workflow request not found: {request_id}"
        )
    if not synthesis_request_is_ready(request):
        raise WorkflowSynthesisError(
            "workflow synthesis requires a ready request"
        )
    requirement_ref = str(request.get("requirement_spec_ref") or "")
    requirement_digest = str(request.get("requirement_spec_digest") or "")
    try:
        requirement = hydrate_workflow_requirement(state_dir, request)
    except WorkflowRequestError as exc:
        raise WorkflowSynthesisError(str(exc)) from exc
    revision = int(request.get("revision") or 0)
    workflow_run_id = f"workflow-request:{request_id}:r{revision}"
    operation_id = stable_operation_id(
        workflow_run_id=workflow_run_id,
        parent_stage_id="workflow-synthesis",
        operation_key="select-flow",
        operation_type=WORKFLOW_SYNTHESIS_OPERATION_TYPE,
    )
    catalog = _admission_catalog(config, project_root)
    operation_request = {
        "request_id": request_id,
        "request_revision": revision,
        "requirement_ref": requirement_ref,
        "requirement_digest": requirement_digest,
        **admission_contract(catalog),
        "adapter_skill_plan_ref": str(
            request.get("skill_adapter_plan_ref") or ""
        ),
        "project_constraints": list(requirement.get("constraints") or []),
        "access": "read_only",
        "actor": actor,
        "backend": backend,
        "operation_context": dict(operation_context or {}),
    }
    return {
        "request": request,
        "requirement": requirement,
        "requirement_ref": requirement_ref,
        "requirement_digest": requirement_digest,
        "revision": revision,
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "catalog": catalog,
        "operation_request": operation_request,
    }


def _hydrate_operation_request(
    state_dir: Path,
    operation: Mapping[str, Any],
) -> dict[str, Any]:
    descriptor = operation.get("request_ref")
    if not isinstance(descriptor, Mapping):
        raise WorkflowSynthesisError(
            "workflow synthesis operation has no request ref"
        )
    payload = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    request = payload.get("request") if isinstance(payload, Mapping) else None
    if not isinstance(request, Mapping):
        raise WorkflowSynthesisError(
            "workflow synthesis operation request is unreadable"
        )
    return dict(request)


def _build_synthesis_proposal(
    *,
    state_dir: Path,
    outcome: WorkflowSynthesisOutcome,
    operation_context: Mapping[str, Any],
    actor: str,
) -> dict[str, Any]:
    from zf.runtime.workflow_synthesis_proposal import (
        WorkflowSynthesisProposalError,
        build_synthesis_proposal,
    )

    try:
        return build_synthesis_proposal(
            state_dir=state_dir,
            result=outcome.result,
            result_ref=outcome.result_ref,
            operation_context=operation_context,
            actor=actor,
        )
    except WorkflowSynthesisProposalError as exc:
        raise WorkflowSynthesisError(str(exc)) from exc


def _invoke_synthesis_agent(
    *,
    state_dir: Path,
    project_root: Path,
    request: Mapping[str, Any],
    requirement: Mapping[str, Any],
    operation_id: str,
    backend: str,
    agent: Any | None,
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    if not backend:
        raise WorkflowSynthesisError(
            "synthesis backend is unavailable; use the manual FlowSpec path"
        )
    if agent is None:
        from zf.web.headless_agent import KanbanHeadlessAgent

        agent = KanbanHeadlessAgent(
            state_dir=state_dir,
            project_root=project_root,
        )
    prompt = _synthesis_prompt(
        request=request,
        requirement=requirement,
        catalog=catalog,
    )
    result = agent.run_turn(
        backend=backend,
        message=prompt,
        scope="project",
        thread_key=f"workflow-synthesis:{operation_id}",
        context={
            "project_id": str(request.get("project_id") or ""),
            "conversation_id": str(request.get("request_id") or ""),
            "turn_id": operation_id,
        },
        permission_profile="read_only",
    )
    if not bool(getattr(result, "ok", False)):
        reason = str(
            getattr(result, "error", "")
            or getattr(result, "status", "")
            or "provider unavailable"
        )
        raise WorkflowSynthesisError(
            f"workflow synthesis provider failed: {reason}"
        )
    reply = str(getattr(result, "reply", "") or "").strip()
    try:
        parsed = json.loads(reply)
    except json.JSONDecodeError as exc:
        raise WorkflowSynthesisError(
            "workflow synthesis provider returned invalid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise WorkflowSynthesisError(
            "workflow synthesis provider result must be an object"
        )
    return parsed


def _admit_result(
    result: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    catalog: Mapping[str, Any],
    state_dir: Path,
    operation_id: str,
) -> dict[str, Any]:
    unknown = sorted(set(result) - _RESULT_KEYS)
    if unknown:
        raise WorkflowSynthesisError(
            "workflow synthesis result has unsupported fields: "
            + ", ".join(unknown)
        )
    if str(result.get("schema_version") or "") != WORKFLOW_SYNTHESIS_RESULT_SCHEMA:
        raise WorkflowSynthesisError(
            f"workflow synthesis schema_version must be "
            f"{WORKFLOW_SYNTHESIS_RESULT_SCHEMA}"
        )
    request_id = str(request.get("request_id") or "")
    revision = int(request.get("revision") or 0)
    requirement_ref = str(request.get("requirement_spec_ref") or "")
    requirement_digest = str(request.get("requirement_spec_digest") or "")
    if str(result.get("request_id") or "") != request_id:
        raise WorkflowSynthesisError("workflow synthesis request_id is stale")
    if int(result.get("request_revision") or 0) != revision:
        raise WorkflowSynthesisError(
            "workflow synthesis request revision is stale"
        )
    if str(result.get("requirement_ref") or "") != requirement_ref or str(
        result.get("requirement_digest") or ""
    ) != requirement_digest:
        raise WorkflowSynthesisError(
            "workflow synthesis requirement identity is stale"
        )
    flow_family = _canonical_flow_family(result.get("selected_flow_family"))
    if flow_family not in ALLOWED_FLOW_FAMILIES:
        raise WorkflowSynthesisError(
            f"unsupported workflow flow family: {flow_family!r}"
        )
    short_spec = result.get("short_flow_spec")
    if not isinstance(short_spec, Mapping):
        raise WorkflowSynthesisError(
            "workflow synthesis short_flow_spec must be an object"
        )
    unknown_spec = sorted(set(short_spec) - _FLOW_SPEC_KEYS)
    if unknown_spec:
        raise WorkflowSynthesisError(
            "short FlowSpec has unsupported fields: "
            + ", ".join(unknown_spec)
        )
    if _canonical_flow_family(short_spec.get("flow_family")) != flow_family:
        raise WorkflowSynthesisError(
            "short FlowSpec family does not match selected flow family"
        )
    parameters = short_spec.get("parameters") or {}
    if not isinstance(parameters, Mapping):
        raise WorkflowSynthesisError(
            "short FlowSpec parameters must be an object"
        )
    unknown_parameters = sorted(set(parameters) - _FLOW_PARAMETER_KEYS)
    if unknown_parameters:
        raise WorkflowSynthesisError(
            "short FlowSpec parameters contain unsupported fields: "
            + ", ".join(unknown_parameters)
        )
    lanes = parameters.get("lanes")
    if lanes is not None and (
        isinstance(lanes, bool)
        or not isinstance(lanes, int)
        or lanes < 1
        or lanes > 64
    ):
        raise WorkflowSynthesisError(
            "short FlowSpec lanes must be an integer between 1 and 64"
        )
    pipeline = parameters.get("task_pipeline")
    if pipeline is not None:
        from zf.runtime.workflow_task_pipeline_inputs import validate_prd_task_pipeline
        try:
            validate_prd_task_pipeline(pipeline, family=flow_family, lanes=lanes)
        except ValueError as exc:
            raise WorkflowSynthesisError(str(exc)) from exc
    for key in ("strictness", "pattern_id"):
        value = parameters.get(key)
        if value is not None and (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 128
        ):
            raise WorkflowSynthesisError(
                f"short FlowSpec {key} must be a non-empty bounded string"
            )
    rationale = str(result.get("decision_rationale") or "").strip()
    if not rationale:
        raise WorkflowSynthesisError(
            "workflow synthesis decision_rationale is required"
        )
    requested_roles = _strings(result.get("requested_roles"))
    requested_skills = _strings(result.get("requested_skills"))
    requested_profiles = _strings(result.get("requested_profiles"))
    from zf.runtime.workflow_registered_controller import admit_capabilities
    try:
        admit_capabilities(parameters, flow_family, catalog, requested_roles, requested_skills, requested_profiles)
    except ValueError as exc:
        raise WorkflowSynthesisError(str(exc)) from exc
    completion = result.get("completion_profile") or {}
    if not isinstance(completion, Mapping):
        raise WorkflowSynthesisError(
            "workflow synthesis completion_profile must be an object"
        )
    unknown_completion = sorted(set(completion) - _COMPLETION_KEYS)
    if unknown_completion:
        raise WorkflowSynthesisError(
            "completion_profile has unsupported fields: "
            + ", ".join(unknown_completion)
        )
    for key in ("delivery_policy", "completion_threshold"):
        value = completion.get(key)
        if value is not None and (
            not isinstance(value, str)
            or len(value) > 128
        ):
            raise WorkflowSynthesisError(
                f"completion_profile {key} must be a bounded string"
            )
    required_artifacts = completion.get("required_artifacts")
    if required_artifacts is not None and (
        not isinstance(required_artifacts, list)
        or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 256
            for item in required_artifacts
        )
    ):
        raise WorkflowSynthesisError(
            "completion_profile required_artifacts must be a list of "
            "bounded strings"
        )
    intent = str(short_spec.get("intent") or "").strip()
    template = str(short_spec.get("template") or "").strip()
    try:
        generic_spec, completion, generic_digest = (
            admit_generic_workflow_selection(
                flow_family=flow_family,
                intent=intent,
                template=template,
                parameters=parameters,
                completion=completion,
                required_artifacts=required_artifacts,
                catalog=catalog,
                requested_roles=requested_roles,
            )
        )
    except GenericWorkflowSynthesisError as exc:
        raise WorkflowSynthesisError(str(exc)) from exc
    short_spec_body = {
        "schema_version": "workflow-short-flow-spec.v1",
        "request_id": request_id,
        "request_revision": revision,
        "flow_family": flow_family,
        "purpose": str(short_spec.get("purpose") or "").strip(),
        "parameters": dict(parameters),
        **({"intent": intent} if intent else {}),
        **({"template": template} if template else {}),
        **({
            "generic_workflow_spec": generic_spec,
            "generic_workflow_contract_source_digest": generic_digest,
        } if generic_spec else {}),
    }
    short_spec_ref = write_immutable_json_sidecar(
        state_dir,
        short_spec_body,
        root=f"workflow/synthesis/{_safe_component(request_id)}/flow-specs",
        kind="workflow_short_flow_spec",
        schema_version="workflow-short-flow-spec.v1",
        created_by="workflow-synthesis-admission",
    )
    return {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "operation_id": operation_id,
        "request_id": request_id,
        "request_revision": revision,
        "requirement_ref": requirement_ref,
        "requirement_digest": requirement_digest,
        "selected_flow_family": flow_family,
        "short_flow_spec_ref": short_spec_ref,
        "short_flow_spec_digest": str(short_spec_ref.get("sha256") or ""),
        "decision_rationale": rationale,
        "assumptions": _strings(result.get("assumptions")),
        "open_questions": _strings(result.get("open_questions")),
        "requested_roles": requested_roles,
        "requested_skills": requested_skills,
        "requested_profiles": requested_profiles,
        "completion_profile": dict(completion),
        "risk_hints": _strings(result.get("risk_hints")),
    }


def _replay_settled_synthesis(
    *,
    state_dir: Path,
    writer: EventWriter,
    request: Mapping[str, Any],
    operation_id: str,
    request_hash: str,
    actor: str,
) -> WorkflowSynthesisOutcome:
    operation = load_workflow_operation(writer.event_log, operation_id) or {}
    descriptor = operation.get("admitted_call_result_ref")
    if not isinstance(descriptor, Mapping):
        raise WorkflowSynthesisError(
            "settled workflow synthesis has no admitted result"
        )
    result = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    if not isinstance(result, dict):
        raise WorkflowSynthesisError(
            "settled workflow synthesis result is unreadable"
        )
    projection = load_workflow_request(
        state_dir,
        str(request.get("request_id") or ""),
    )
    if (
        not projection.get("synthesis_digest")
        and str(projection.get("status") or "") in {"ready", "clarifying"}
    ):
        projection = bind_workflow_synthesis_result(
            state_dir,
            request_id=str(request.get("request_id") or ""),
            request_revision=int(request.get("revision") or 0),
            requirement_digest=str(
                request.get("requirement_spec_digest") or ""
            ),
            synthesis_ref=dict(descriptor),
            synthesis_digest=str(descriptor.get("sha256") or ""),
            selected_flow_family=str(
                result.get("selected_flow_family") or ""
            ),
            open_questions=list(result.get("open_questions") or []),
            actor=actor,
            writer=writer,
        )
    return WorkflowSynthesisOutcome(
        operation_id=operation_id,
        request_hash=request_hash,
        result=result,
        result_ref=dict(descriptor),
        request_projection=projection,
        replayed=True,
    )


__all__ = [
    "ALLOWED_FLOW_FAMILIES",
    "WORKFLOW_SYNTHESIS_RESULT_SCHEMA",
    "WorkflowSynthesisError",
    "WorkflowSynthesisOutcome",
    "WorkflowSynthesisQueueOutcome",
    "consume_workflow_synthesis_operations",
    "enqueue_workflow_synthesis",
    "run_workflow_synthesis",
]
