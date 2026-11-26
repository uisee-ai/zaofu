"""Task-scoped OA verification plans and their versioned correction checkpoint."""

from __future__ import annotations

from typing import Any, Mapping

from zf.runtime.orchestrator_agent_contracts import OrchestratorAgentContractError

VERIFY_WAIT = "oa_verification_assignment_required"


def verify_seats(runtime: Any) -> set[str]:
    from zf.runtime.task_pipeline_reconciler import task_pipeline_policy

    policy = task_pipeline_policy(runtime.config, flow_kind="prd") or {}
    pool = set(policy.get("pools", {}).get("verify", {}).get("role_instances", []))
    writers = set(policy.get("pools", {}).get("impl", {}).get("role_instances", []))
    readers = {role.instance_id for role in runtime.config.roles if role.role_kind == "reader"}
    return pool & readers - writers


def validate_verify_units(runtime: Any, run_plan: Mapping, request: Mapping) -> None:
    """Validate the immutable dispatch proposal at submission, and recheck it against live authority at admission."""
    if request.get("candidate_reader_assignments_digest") and (
        run_plan.get("candidate_reader_assignments_digest") != request["candidate_reader_assignments_digest"]
    ):
        raise OrchestratorAgentContractError("adopt requires the current approved Candidate Reader assignments digest")
    if request.get("verify_assignment_mode") != "oa_run_plan":
        return
    seats = set(request.get("verify_assignment_seats", []))
    if runtime is not None:
        seats &= verify_seats(runtime)
    delegates = {d["work_unit_id"]: d["preferred_role_refs"] for d in run_plan.get("delegation", [])}
    seen = set()
    target = request.get("verify_assignment_task")
    incident = request.get("verify_specialist_incident") or {}
    offered = {(r["obligation_id"], r["operation_id"]) for r in incident.get("available_opinions", [])}
    if incident and runtime is not None:
        current = _current_assignment(runtime.event_log.read_all(), request, target)
        if current != incident["prior_execution_plan_ref"]:
            raise OrchestratorAgentContractError("a later OA decision superseded this specialist correction")
    for unit in run_plan.get("graph", {}).get("work_units", []):
        stages = unit.get("stage_ids") or []
        if "verify" not in stages and "prd-lanes-verify" not in stages:
            if target:
                raise OrchestratorAgentContractError("scoped Verify correction may only assign Verify")
            continue
        roles = delegates.get(unit["work_unit_id"]) or []
        task_ids = unit.get("task_ids")
        scope = unit.get("verification_scope")
        if (len(stages) != 1 or not isinstance(task_ids, list) or not task_ids
                or not all(isinstance(item, str) and item.strip() for item in task_ids)
                or len(set(task_ids)) != len(task_ids) or seen.intersection(task_ids)
                or not roles or set(roles) - seats):
            raise OrchestratorAgentContractError("Verify needs distinct task assignments and independent registered Reader seats")
        if not isinstance(scope, dict) or set(scope) != {"acceptance_ids", "required_evidence"}:
            raise OrchestratorAgentContractError("Verify work unit requires verification_scope.acceptance_ids and required_evidence")
        for values in scope.values():
            if (not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values)
                    or len(set(values)) != len(values)):
                raise OrchestratorAgentContractError("verification_scope requires distinct nonempty strings")
        if "*" in scope["acceptance_ids"] and scope["acceptance_ids"] != ["*"]:
            raise OrchestratorAgentContractError("use * alone to select every Task Verify acceptance criterion")
        expected = request.get("verify_assignment_acceptance")
        if target and expected is not None and scope["acceptance_ids"] != ["*"] and set(scope["acceptance_ids"]) != set(expected):
            raise OrchestratorAgentContractError("scoped Verify correction must cover this version's Task Verify acceptance")
        specialists = unit.get("specialists", [])
        if not isinstance(specialists, list):
            raise OrchestratorAgentContractError("Verify specialists must be a list")
        for member in specialists:
            if not isinstance(member, dict):
                raise OrchestratorAgentContractError("Verify specialist must be a member contract")
            if "verify_specialist_patterns" in request:
                registered = request["verify_specialist_patterns"].get(member.get("role"), [])
                if not registered or (member.get("pattern_id") and member["pattern_id"] not in registered):
                    raise OrchestratorAgentContractError("Verify specialist is outside the approved Reader pattern catalog")
            if "reuse_operation_id" in member and (
                not isinstance(member.get("obligation_id"), str) or not isinstance(member["reuse_operation_id"], str)
                or (member["obligation_id"], member["reuse_operation_id"]) not in offered
            ):
                raise OrchestratorAgentContractError("specialist reuse must name a currently offered obligation and operation")
        if runtime is not None and unit.get("specialists"):
            from zf.runtime.task_verify_specialists import validate_specialists
            errors = validate_specialists(runtime, unit, roles)
            if errors:
                raise OrchestratorAgentContractError(errors[0])
        seen.update(task_ids)
    if target and seen != {target}:
        raise OrchestratorAgentContractError("scoped Verify correction must cover only its requested task")


def _verify_assignment_acceptance(task, revision: str) -> list[str]:
    """Return the exact local ACs offered to OA and enforced at dispatch."""
    from zf.runtime.frozen_audit_scope import bind_frozen_audit_scope
    from zf.runtime.task_contract_snapshot import normalize_acceptance_criteria, snapshot_verification_route
    from zf.runtime.task_pipeline_entry import task_pipeline_entry_mode
    from zf.runtime.verification_commands import acceptance_required_for_stage, task_contract_verification_commands

    contract = task.contract
    criteria = normalize_acceptance_criteria(contract.acceptance_criteria or [contract.acceptance], task_id=task.id,
                                             contract_revision=revision, verification_tiers=contract.verification_tiers)
    evidence = contract.evidence_contract or {}
    if (task_pipeline_entry_mode(task) != "verify_only" or evidence.get("runtime_only") is not True
            or evidence.get("write_free") is not True or contract.scope != []):
        return [c["acceptance_id"] for c in criteria if c["verification_owner"] == "task_verify"]
    try:
        commands = task_contract_verification_commands(contract)
        bind_frozen_audit_scope(evidence, criteria, commands, task_id=task.id, write_paths=contract.scope)
        owner, _ = snapshot_verification_route(criteria, commands=commands, task_id=task.id)
    except ValueError:
        return []  # An invalid audit contract proves no exact AC grant.
    return [c["acceptance_id"] for c in criteria if c["mandatory"]
            and acceptance_required_for_stage(c, verification_owner=owner)]


def current_scope(runtime: Any, task_id: str, context: Mapping, admission: Mapping,
                  unit: Mapping, role_ids: list[str]) -> dict | None:
    from zf.runtime.task_contract_snapshot import effective_contract_revision

    task = runtime.task_store.get(task_id)
    if not task:
        return None
    scope = unit.get("verification_scope") or {}
    revision = effective_contract_revision(task)
    bound = (admission.get("verify_assignment_revision") or
             (context.get("task_contract_revisions") or {}).get(task_id))
    if not revision or bound != revision or not role_ids or not scope:
        return None
    selected = scope["acceptance_ids"]
    if selected != ["*"]:
        if not selected or set(selected) != set(_verify_assignment_acceptance(task, revision)):
            return None
    return {"verification_scope": scope, "contract_revision": revision,
            **({"specialists": unit["specialists"]} if unit.get("specialists") else {})}


def request_verification_corrections(runtime, projection, contexts, events) -> None:
    waiting = [row["task_id"] for row in projection["tasks"] if VERIFY_WAIT in row["blockers"]]
    if not waiting:
        return
    by_id = {event.id: event for event in events}
    rejected = {e.payload.get("operation_id"): e for e in events
                if e.type == "orchestrator.semantic.decision.rejected" and e.origin == "kernel"
                and e.actor == "zf-cli" and e.payload.get("status") == "rejected"}
    awaiting_release = _pending_impl_releases(events, contexts, set(waiting))
    for task_id in waiting:
        if task_id in awaiting_release:
            continue
        context = contexts[task_id]
        source = by_id.get(context["generation_admitted_event_id"])
        task = runtime.task_store.get(task_id)
        if not source or not task or task.active_dispatch_id:
            continue
        prepared = _request_correction(runtime, task, context, source, {})
        rejection = rejected.get(prepared.operation_id) if prepared else None
        if rejection and rejection.payload.get("reason") == "execution_target_changed:" + task_id:
            release = _released_verify_predecessor(runtime, task, context, prepared, events)
            if release:
                _request_correction(runtime, task, context, source, {
                    "verify_assignment_release_event_id": release.id,
                    "verify_assignment_rejection_event_id": rejection.id,
                })


def _pending_impl_releases(events, contexts, waiting):
    """Hold an in-flight physical Impl. Terminal failures keep their existing recovery route."""
    last_impl = {}
    last_sent = {}
    released = set()
    terminals = {"task.attempt.succeeded", "task.attempt.failed", "task.attempt.superseded", "task.attempt.deadlettered"}
    relevant = terminals | {"task.pipeline.stage.dispatched", "task.attempt.sent"}
    for event in events:
        if (event.type not in relevant
                or event.origin != "kernel" or event.actor != "orchestrator"):
            continue
        p = event.payload
        task_id = p.get("task_id")
        if task_id not in waiting or p.get("workflow_run_id") != contexts[task_id]["workflow_run_id"]:
            continue
        placement = tuple(p.get(key) for key in ("task_id", "operation_id", "attempt_id", "dispatch_id"))
        if event.type == "task.pipeline.stage.dispatched":
            if (p.get("task_pipeline_stage") == "impl"
                    and p.get("task_map_generation") == contexts[task_id]["task_map_generation"]):
                last_impl[task_id] = p.get("operation_id")
        elif event.type == "task.attempt.sent":
            if all(placement):
                last_sent[(task_id, p["operation_id"])] = placement
        elif all(placement):
            released.add(placement)
    return {task_id for task_id, operation in last_impl.items()
            if (placement := last_sent.get((task_id, operation))) and placement not in released}


def _released_verify_predecessor(runtime, task, context, prepared, events):
    """Reopen only an old scoped input whose exact settled Impl dispatch has since been released."""
    from zf.runtime.orchestrator_agent_execution_policy import operation_bindings, task_execution_binding
    from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref

    if prepared.status != "settled" or not prepared.context:
        return None
    descriptor = prepared.context.input_body.get("execution_context_ref")
    if not descriptor:
        return None
    try:
        frozen = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    except (OSError, SidecarRefError):
        return None
    original = frozen.get("task_bindings", {}).get(task.id) or {}
    dispatch = original.get("active_dispatch_id")
    if not dispatch or task_execution_binding(task) != {**original, "active_dispatch_id": ""}:
        return None
    expected = frozen.get("operation_bindings", {})
    operation = expected.get(task.id) or {}
    run = context["workflow_run_id"]
    if (operation.get("task_pipeline_stage") != "impl" or operation.get("status") != "settled"
            or operation.get("task_map_generation") != context["task_map_generation"]
            or operation_bindings(events, run, {task.id}) != expected):
        return None
    source_id = (operation.get("admitted_control_result_ref") or {}).get("source_event_id")
    attempt = operation.get("active_attempt_id")
    if not source_id or not attempt or not operation.get("operation_id"):
        return None
    release = None
    for event in reversed(events):
        p = event.payload
        if (event.origin != "kernel" or event.actor != "orchestrator" or p.get("workflow_run_id") != run
                or p.get("task_id") != task.id or p.get("operation_id") != operation["operation_id"]):
            continue
        same_placement = p.get("dispatch_id") == dispatch and p.get("attempt_id") == attempt
        if event.type == "task.attempt.sent":
            return release if same_placement else None
        if same_placement and release is None:
            if event.type in {"task.attempt.failed", "task.attempt.superseded", "task.attempt.deadlettered"}:
                return None
            if event.type == "task.attempt.succeeded" and p.get("status") == "succeeded" and p.get("source_event_id") == source_id:
                release = event
    return release


def _current_assignment(events, context, task_id):
    from zf.runtime.orchestrator_agent_run_plan import current_run_plan_admission
    return current_run_plan_admission(events, verify_task=task_id, require_kernel=True,
        **{k: context[k] for k in ("workflow_run_id", "task_map_generation", "plan_artifact_package_ref",
                                     "plan_artifact_package_digest")}).get("execution_plan_ref")


def request_specialist_correction(runtime, *, task_id, context, incident, events):
    """Preserve the failed invocation and completed options as inputs to one bounded OA correction."""
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.workflow_operation import reduce_workflow_operations

    source = next((e for e in events if e.id == incident["source_event_id"]), None)
    if not source:
        return None
    if source.type == "workflow.invoke.rejected":
        if not (source.actor == "zf-cli" and source.origin in {"", "kernel"} and any(
                e.id == source.causation_id and e.type == "workflow.invoke.requested" and e.origin == "kernel"
                and e.payload.get("workflow_operation_id") == incident["operation_id"] for e in events)):
            return None
    elif source.origin != "kernel":
        return None
    task = runtime.task_store.get(task_id)
    run = context["workflow_run_id"]
    operations = reduce_workflow_operations(events)
    failed = operations.get(incident["operation_id"]) or {}
    if (not task or failed.get("workflow_run_id") != run or failed.get("task_id") != task_id
            or failed.get("operation_type") != "task_verify_specialist"
            or _current_assignment(events, context, task_id) != incident["prior_execution_plan_ref"]):
        return None
    options = []
    for row in incident.get("available_opinions", []):
        prior = operations.get(row["operation_id"]) or {}
        if (prior.get("workflow_run_id") == run and prior.get("task_id") == task_id
                and prior.get("operation_type") == "task_verify_specialist" and prior.get("status") == "settled"
                and prior.get("admitted_call_result_ref")):
            options.append({**row, "result_ref": prior["admitted_call_result_ref"]})
    bound = {**incident, "available_opinions": options}
    descriptor = write_immutable_json_sidecar(runtime.state_dir,
        {"schema_version": "verify-specialist-incident.v1", "workflow_run_id": run, "task_id": task_id,
         "task_map_generation": context["task_map_generation"], **bound, "source_event_type": source.type},
        root="orchestrator-agent/verify-incidents", kind="verify_specialist_incident",
        schema_version="verify-specialist-incident.v1", created_by="task-pipeline", source_event_id=source.id)
    return _request_correction(runtime, task, context, source, {"verify_specialist_incident": bound,
                               "verify_specialist_incident_ref": descriptor})


def _request_correction(runtime, task, context, source, extra):
    from zf.runtime.orchestrator_agent_operations import request_orchestrator_agent_checkpoint
    from dataclasses import asdict
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
    from zf.runtime.task_contract_snapshot import effective_contract_revision

    from zf.runtime.pause_lifecycle import is_run_dispatch_paused

    if is_run_dispatch_paused(runtime.event_log.read_all(), context["workflow_run_id"]):
        return None
    task_id = task.id
    package = hydrate_plan_artifact_package(runtime.state_dir, {
        "ref": context["plan_artifact_package_ref"], "sha256": context["plan_artifact_package_digest"],
    })
    revision = effective_contract_revision(task)
    contract_ref = write_immutable_json_sidecar(runtime.state_dir,
        {"schema_version": "oa-verify-contract-input.v1", "task_id": task_id,
         "workflow_run_id": context["workflow_run_id"], "task_map_generation": context["task_map_generation"],
         "contract_revision": revision, "contract": asdict(task.contract)},
        root="orchestrator-agent/verify-contracts", kind="verify_contract",
        schema_version="oa-verify-contract-input.v1", created_by="task-pipeline", source_event_id=source.id)
    return request_orchestrator_agent_checkpoint(runtime, checkpoint="pre_impl", checkpoint_policy="blocking",
        workflow_run_id=context["workflow_run_id"], source_event=source, payload={
            **context, **extra, "goal_id": context.get("feature_id") or context.get("pdd_id") or package.get("goal_id"),
            "run_contract_ref": package.get("run_contract_ref"), "run_contract_digest": package.get("run_contract_digest"),
            "run_contract_sha256": package.get("run_contract_sha256"),
            "verify_assignment_mode": "oa_run_plan", "verify_assignment_task": task_id,
            "verify_assignment_seats": sorted(verify_seats(runtime)),
            "verify_assignment_acceptance": _verify_assignment_acceptance(task, revision),
            "verify_assignment_revision": revision, "verify_assignment_contract_ref": contract_ref,
        })
