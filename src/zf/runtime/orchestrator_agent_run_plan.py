"""Plan-bound pre-implementation admission and context routing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_contracts import (
    OrchestratorAgentContractError,
)
from zf.runtime.orchestrator_agent_operations import (
    request_orchestrator_agent_checkpoint,
)
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    orchestration_flow_kind,
)
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


RUN_PLAN_ADMITTED = "orchestrator.run_plan.admitted"
EXECUTION_PLAN_SCHEMA = "orchestrator-execution-plan.v1"


@dataclass(frozen=True)
class PreImplCheckpointState:
    enabled: bool
    blocking: bool
    satisfied: bool
    operation_id: str = ""
    status: str = ""


def pre_impl_checkpoint_state(
    runtime: Any,
    *,
    stage_id: str,
    trigger_event: ZfEvent,
    loaded: Any,
    trace_id: str,
) -> PreImplCheckpointState:
    """Ensure one current Plan-bound operation before implementation."""

    flow_kind = orchestration_flow_kind(
        {"stage_id": stage_id},
        loaded,
        trigger_event,
    )
    policy = checkpoint_policy(
        runtime.config,
        "pre_impl",
        flow_kind=flow_kind,
    )
    if not policy:
        return PreImplCheckpointState(False, False, False)
    workflow_run_id = str(
        getattr(loaded, "workflow_run_id", "") or trace_id
    )
    package_ref = str(getattr(loaded, "plan_artifact_package_ref", "") or "")
    package_digest = str(
        getattr(loaded, "plan_artifact_package_digest", "") or ""
    )
    generation = str(getattr(loaded, "task_map_generation", "") or "")
    admitted = current_run_plan_admission(
        runtime.event_log.read_all(),
        workflow_run_id=workflow_run_id,
        plan_artifact_package_ref=package_ref,
        plan_artifact_package_digest=package_digest,
        task_map_generation=generation,
    )
    if admitted:
        descriptor = admitted.get("execution_plan_ref")
        if not isinstance(descriptor, Mapping):
            raise OrchestratorAgentContractError(
                "admitted pre_impl execution plan ref is missing"
            )
        execution_plan = hydrate_sidecar_ref(
            runtime.state_dir, dict(descriptor)
        ).payload
        if not isinstance(execution_plan, Mapping) or str(
            execution_plan.get("schema_version") or ""
        ) != EXECUTION_PLAN_SCHEMA:
            raise OrchestratorAgentContractError(
                "admitted pre_impl execution plan is invalid"
            )
        return PreImplCheckpointState(
            enabled=True,
            blocking=policy == "blocking",
            satisfied=True,
            operation_id=str(admitted.get("operation_id") or ""),
            status="admitted",
        )
    if not package_ref or not package_digest or not generation:
        raise OrchestratorAgentContractError(
            "pre_impl requires a current Plan Artifact Package and generation"
        )
    package = hydrate_plan_artifact_package(
        runtime.state_dir,
        {"ref": package_ref, "sha256": package_digest},
    )
    event_payload = (
        trigger_event.payload
        if isinstance(trigger_event.payload, Mapping)
        else {}
    )
    goal_id = str(
        getattr(loaded, "feature_id", "")
        or getattr(loaded, "pdd_id", "")
        or event_payload.get("goal_id")
        or event_payload.get("feature_id")
        or ""
    )
    if not goal_id:
        raise OrchestratorAgentContractError("pre_impl requires goal_id")
    payload = {
        **dict(event_payload),
        "workflow_run_id": workflow_run_id,
        "flow_kind": flow_kind,
        "goal_id": goal_id,
        "stage_id": stage_id,
        "plan_revision": str(package.get("plan_revision") or "1"),
        "task_map_generation": generation,
        "plan_artifact_package_id": str(
            getattr(loaded, "plan_artifact_package_id", "") or ""
        ),
        "plan_artifact_package_ref": package_ref,
        "plan_artifact_package_digest": package_digest,
        "task_map_ref": str(getattr(loaded, "task_map_ref", "") or ""),
        "run_contract_ref": str(package.get("run_contract_ref") or ""),
        "run_contract_sha256": str(package.get("run_contract_sha256") or ""),
        "run_contract_digest": str(package.get("run_contract_digest") or ""),
    }
    prepared = request_orchestrator_agent_checkpoint(
        runtime,
        checkpoint="pre_impl",
        checkpoint_policy=policy,
        workflow_run_id=workflow_run_id,
        source_event=trigger_event,
        payload=payload,
    )
    return PreImplCheckpointState(
        enabled=True,
        blocking=policy == "blocking",
        satisfied=False,
        operation_id=prepared.operation_id,
        status=prepared.status,
    )


def validate_run_plan_admission(
    runtime: Any,
    *,
    run_plan: Mapping[str, Any],
    request: Mapping[str, Any],
) -> None:
    """Validate config capabilities and source bounds for one typed plan."""

    identity = run_plan.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    expected = {
        "operation_id": str(request.get("operation_id") or ""),
        "workflow_run_id": str(request.get("workflow_run_id") or ""),
        "goal_id": str(request.get("goal_id") or ""),
        "effective_config_digest": str(
            request.get("effective_config_digest") or ""
        ),
        "run_contract_ref": str(request.get("run_contract_ref") or ""),
        "run_contract_digest": str(request.get("run_contract_digest") or ""),
    }
    for key, value in expected.items():
        if not value or str(identity.get(key) or "") != value:
            raise OrchestratorAgentContractError(
                f"run_plan identity mismatch for {key}"
            )
    _validate_delegation(runtime, run_plan)
    source_manifest = _request_source_manifest(runtime, request)
    allowed = {
        (str(row.get("ref") or ""), str(row.get("sha256") or ""))
        for row in source_manifest.get("sources") or []
        if isinstance(row, Mapping)
    }
    for route in run_plan.get("context_routes") or []:
        if not isinstance(route, Mapping):
            continue
        for descriptor in route.get("required_sources") or []:
            if not isinstance(descriptor, Mapping):
                continue
            key = (
                str(descriptor.get("ref") or ""),
                str(descriptor.get("sha256") or ""),
            )
            if key not in allowed:
                raise OrchestratorAgentContractError(
                    "context route source is outside the checkpoint manifest: "
                    + key[0]
                )


def compile_run_plan_admission(
    runtime: Any,
    *,
    event: ZfEvent,
    decision: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist the admitted plan and its bounded executable projection."""

    run_plan = decision.get("run_plan")
    if not isinstance(run_plan, Mapping):
        raise OrchestratorAgentContractError("run-plan adopt requires run_plan")
    request = _operation_request(runtime, str(outcome.get("operation_id") or ""))
    source_manifest = _request_source_manifest(runtime, request)
    source_by_descriptor = {
        (str(row.get("ref") or ""), str(row.get("sha256") or "")): dict(row)
        for row in source_manifest.get("sources") or []
        if isinstance(row, Mapping)
    }
    plan_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        dict(run_plan),
        root="orchestrator-agent/run-plans",
        kind="run_orchestration_plan",
        schema_version="run-orchestration-plan.v1",
        created_by="orchestrator-agent-admission",
        source_event_id=event.id,
    )
    compiled_routes: list[dict[str, Any]] = []
    for route in run_plan.get("context_routes") or []:
        if not isinstance(route, Mapping):
            continue
        work_unit_id = str(route.get("work_unit_id") or "")
        sources: list[dict[str, Any]] = []
        for descriptor in route.get("required_sources") or []:
            if not isinstance(descriptor, Mapping):
                continue
            source = source_by_descriptor.get((
                str(descriptor.get("ref") or ""),
                str(descriptor.get("sha256") or ""),
            ))
            if source is None:
                raise OrchestratorAgentContractError(
                    "context route source disappeared during compilation"
                )
            sources.append(source)
        compiled_routes.append({
            "work_unit_id": work_unit_id,
            "required_sources": sources,
            "return_policy": str(route.get("return_policy") or "selective"),
        })
    decision_identity = decision.get("identity")
    decision_identity = (
        decision_identity if isinstance(decision_identity, Mapping) else {}
    )
    effective_config_ref = request.get("effective_config_ref")
    effective_config_ref = (
        dict(effective_config_ref)
        if isinstance(effective_config_ref, Mapping)
        else {}
    )
    effective_config_digest = str(
        request.get("effective_config_digest")
        or effective_config_ref.get("sha256")
        or ""
    )
    execution_plan = {
        "schema_version": EXECUTION_PLAN_SCHEMA,
        "identity": {
            **dict(run_plan.get("identity") or {}),
            "task_map_generation": str(
                decision_identity.get("task_map_generation") or ""
            ),
            "plan_artifact_package_ref": str(
                decision_identity.get("plan_artifact_package_ref") or ""
            ),
            "plan_artifact_package_digest": str(
                decision_identity.get("plan_artifact_package_digest") or ""
            ),
            "effective_config_ref": str(
                effective_config_ref.get("ref") or ""
            ),
            "effective_config_digest": effective_config_digest,
        },
        "effective_config_ref": effective_config_ref,
        "run_orchestration_plan_ref": plan_ref,
        "work_units": list((run_plan.get("graph") or {}).get("work_units") or []),
        "edges": list((run_plan.get("graph") or {}).get("edges") or []),
        "delegation": list(run_plan.get("delegation") or []),
        "context_routes": compiled_routes,
    }
    execution_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        execution_plan,
        root="orchestrator-agent/execution-plans",
        kind="orchestrator_execution_plan",
        schema_version=EXECUTION_PLAN_SCHEMA,
        created_by="orchestrator-agent-admission",
        source_event_id=event.id,
    )
    payload = {
        "schema_version": "orchestrator-run-plan-admission.v1",
        "workflow_run_id": str(decision_identity.get("workflow_run_id") or ""),
        "goal_id": str(run_plan.get("identity", {}).get("goal_id") or ""),
        "operation_id": str(outcome.get("operation_id") or ""),
        "source_event_id": str(outcome.get("source_event_id") or ""),
        "decision_event_id": event.id,
        "task_map_generation": str(
            decision_identity.get("task_map_generation") or ""
        ),
        "plan_artifact_package_ref": str(
            decision_identity.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            decision_identity.get("plan_artifact_package_digest") or ""
        ),
        "effective_config_ref": effective_config_ref,
        "effective_config_digest": effective_config_digest,
        "run_orchestration_plan_ref": plan_ref,
        "execution_plan_ref": execution_ref,
    }
    admitted = runtime.event_writer.append(ZfEvent(
        type=RUN_PLAN_ADMITTED,
        actor="zf-cli",
        origin="kernel",
        payload=payload,
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    return {
        **dict(outcome),
        "run_plan_event_id": admitted.id,
        "execution_plan_ref": execution_ref,
        "redrive_source_event_id": payload["source_event_id"],
    }


def current_run_plan_admission(
    events: Sequence[ZfEvent],
    *,
    workflow_run_id: str,
    plan_artifact_package_ref: str,
    plan_artifact_package_digest: str,
    task_map_generation: str,
) -> dict[str, Any]:
    for event in reversed(events):
        if event.type != RUN_PLAN_ADMITTED:
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if all((
            str(payload.get("workflow_run_id") or "") == workflow_run_id,
            str(payload.get("plan_artifact_package_ref") or "")
            == plan_artifact_package_ref,
            str(payload.get("plan_artifact_package_digest") or "")
            == plan_artifact_package_digest,
            str(payload.get("task_map_generation") or "")
            == task_map_generation,
        )):
            return dict(payload)
    return {}


def admitted_context_route_sources(
    *,
    state_dir: Path,
    workflow_run_id: str,
    task_id: str,
    stage_id: str,
    task_map_generation: str,
    plan_artifact_package_ref: str,
    plan_artifact_package_digest: str,
) -> list[dict[str, Any]]:
    """Resolve only same-generation context routes for one downstream unit."""

    if not workflow_run_id or not task_map_generation:
        return []
    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    admitted = current_run_plan_admission(
        events,
        workflow_run_id=workflow_run_id,
        plan_artifact_package_ref=plan_artifact_package_ref,
        plan_artifact_package_digest=plan_artifact_package_digest,
        task_map_generation=task_map_generation,
    )
    descriptor = admitted.get("execution_plan_ref")
    if not isinstance(descriptor, Mapping):
        return []
    execution_plan = hydrate_sidecar_ref(
        Path(state_dir), dict(descriptor)
    ).payload
    if not isinstance(execution_plan, Mapping):
        return []
    selected = _selected_work_units(
        execution_plan,
        task_id=task_id,
        stage_id=stage_id,
    )
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for route in execution_plan.get("context_routes") or []:
        if not isinstance(route, Mapping) or str(
            route.get("work_unit_id") or ""
        ) not in selected:
            continue
        work_unit_id = str(route.get("work_unit_id") or "")
        for index, source in enumerate(route.get("required_sources") or []):
            if not isinstance(source, Mapping):
                continue
            key = (
                str(source.get("ref") or ""),
                str(source.get("sha256") or ""),
            )
            if not all(key) or key in seen:
                continue
            seen.add(key)
            sources.append({
                **dict(source),
                "source_id": (
                    f"oa-route-{_safe(work_unit_id)}-{index + 1}"
                ),
                "allowed_paths": ["$"],
            })
    return sources


def _selected_work_units(
    execution_plan: Mapping[str, Any],
    *,
    task_id: str,
    stage_id: str,
) -> set[str]:
    selected: set[str] = set()
    for row in execution_plan.get("work_units") or []:
        if not isinstance(row, Mapping):
            continue
        unit_id = str(row.get("work_unit_id") or "")
        task_ids = _strings(row.get("task_ids"))
        stage_ids = _strings(row.get("stage_ids"))
        if (
            unit_id in {task_id, stage_id}
            or (task_id and task_id in task_ids)
            or (stage_id and stage_id in stage_ids)
        ):
            selected.add(unit_id)
    return selected


def _validate_delegation(runtime: Any, run_plan: Mapping[str, Any]) -> None:
    roles_by_ref: dict[str, list[Any]] = {}
    for role in runtime.config.roles:
        for ref in {
            str(getattr(role, "instance_id", "") or ""),
            str(getattr(role, "name", "") or ""),
        }:
            if ref:
                roles_by_ref.setdefault(ref, []).append(role)
    for row in run_plan.get("delegation") or []:
        if not isinstance(row, Mapping):
            continue
        preferred = _strings(row.get("preferred_role_refs"))
        candidates: list[Any] = []
        for role_ref in preferred:
            matches = list({id(role): role for role in roles_by_ref.get(role_ref, [])}.values())
            if len(matches) != 1:
                reason = "unknown" if not matches else "ambiguous"
                raise OrchestratorAgentContractError(
                    f"delegation role {role_ref!r} is {reason}"
                )
            candidates.append(matches[0])
        capabilities = set(_strings(row.get("capability_refs")))
        if capabilities and not any(
            capabilities <= _role_capabilities(role) for role in candidates
        ):
            raise OrchestratorAgentContractError(
                "delegation capabilities do not match a preferred role: "
                + ", ".join(sorted(capabilities))
            )


def _role_capabilities(role: Any) -> set[str]:
    name = str(getattr(role, "name", "") or "")
    instance_id = str(getattr(role, "instance_id", "") or name)
    role_kind = str(getattr(role, "role_kind", "auto") or "auto")
    backend = str(getattr(role, "backend", "") or "")
    values = {
        name,
        instance_id,
        role_kind,
        f"role:{name}",
        f"role:{instance_id}",
        f"role_kind:{role_kind}",
        f"backend:{backend}",
    }
    values.update(f"stage:{value}" for value in getattr(role, "stages", []) or [])
    values.update(f"skill:{value}" for value in getattr(role, "skills", []) or [])
    values.update(f"tool:{value}" for value in getattr(role, "allowed_tools", []) or [])
    return {value for value in values if value and not value.endswith(":")}


def _request_source_manifest(
    runtime: Any,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    descriptor = request.get("source_manifest_ref")
    if not isinstance(descriptor, Mapping):
        raise OrchestratorAgentContractError("pre_impl source manifest is missing")
    payload = hydrate_sidecar_ref(runtime.state_dir, dict(descriptor)).payload
    if not isinstance(payload, Mapping):
        raise OrchestratorAgentContractError("pre_impl source manifest is invalid")
    return dict(payload)


def _operation_request(runtime: Any, operation_id: str) -> dict[str, Any]:
    from zf.runtime.workflow_operation import load_workflow_operation

    operation = load_workflow_operation(runtime.event_log, operation_id)
    request_ref = operation.get("request_ref") if isinstance(operation, Mapping) else None
    if not isinstance(request_ref, Mapping):
        raise OrchestratorAgentContractError("pre_impl operation request is missing")
    stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
    request = stored.get("request") if isinstance(stored, Mapping) else None
    if not isinstance(request, Mapping):
        raise OrchestratorAgentContractError("pre_impl operation request is invalid")
    return dict(request)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in str(value)
    ).strip(".-") or "item"


__all__ = [
    "EXECUTION_PLAN_SCHEMA",
    "RUN_PLAN_ADMITTED",
    "PreImplCheckpointState",
    "admitted_context_route_sources",
    "compile_run_plan_admission",
    "current_run_plan_admission",
    "pre_impl_checkpoint_state",
    "validate_run_plan_admission",
]
