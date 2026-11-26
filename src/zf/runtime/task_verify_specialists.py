"""Admit the OA's task-scoped specialist evidence before dispatching the formal verifier."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.stage_teams import team_member
from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations, stable_operation_id


def _patterns(config, role_id, requested):
    for stage in config.workflow.stages:
        if requested and stage.id != requested:
            continue
        # A Judge, Candidate or Plan reader can publish privileged workflow transitions.
        # A specialist only submits generic evidence to one independent Task verifier.
        aggregate = stage.aggregate
        if (stage.flow_kind not in {"", "prd"} or stage.trigger != "workflow.invoke.requested" or stage.topology != "fanout_reader"
                or aggregate.mode != "wait_for_all" or aggregate.synth_role or aggregate.success_event
                or aggregate.failure_event or aggregate.pending_event or stage.operation
                or aggregate.child_success_event != "workflow.child.completed"
                or aggregate.child_failure_event != "workflow.child.failed"
                or len(stage.children or stage.roles) != 1):
            continue
        refs = set(stage.roles)
        if stage.children:
            child = stage.children[0]
            if child.task_id:
                continue
            refs.add(child.role_instance or child.role)
        instances = {role.instance_id for role in config.roles if {role.name, role.instance_id} & refs}
        if instances == {role_id}:
            yield stage.id


def specialist_catalog(config) -> dict[str, list[str]]:
    catalog = {}
    for role in config.roles:
        if role.role_kind == "reader" and role.flow_kind in {"", "prd"}:
            patterns = sorted(_patterns(config, role.instance_id, None))
            if patterns:
                catalog[role.instance_id] = patterns
    return catalog


def _members(config, unit, primary_roles):
    raw = unit.get("specialists", [])
    reader_count = sum(role.role_kind == "reader" for role in config.roles)
    if not isinstance(raw, list) or len(raw) > reader_count:
        raise ValueError("Verify specialist count exceeds the registered Reader roster")
    members, ids, seats = [], set(), set(primary_roles)
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Verify specialist must be a member contract")
        member = team_member({k: v for k, v in item.items() if k not in {"pattern_id", "reuse_operation_id"}}, stage="task_verify")
        roles = [r for r in config.roles if r.instance_id == member["role"]]
        if (len(roles) != 1 or roles[0].role_kind != "reader" or roles[0].flow_kind not in {"", "prd"} or member["role"] in seats
                or member["obligation_id"] in ids):
            raise ValueError("Verify specialists need distinct obligations and independent registered reader instances")
        if any(ref not in {"task-contract", "contract"} and not re.fullmatch(r"contract:\$\.[A-Za-z_][A-Za-z0-9_]*", ref)
               for ref in member["input_refs"]):
            raise ValueError("Verify specialist inputs must name task-contract or an exact contract:$.field")
        patterns = list(_patterns(config, member["role"], item.get("pattern_id")))
        if len(patterns) != 1:
            raise ValueError("Verify specialist must select one registered single-reader pattern")
        if "reuse_operation_id" in item:
            reuse = item["reuse_operation_id"]
            if not isinstance(reuse, str) or not reuse.startswith("wop-"):
                raise ValueError("specialist reuse must name an admitted operation")
            member["reuse_operation_id"] = reuse
        members.append({**member, "pattern_id": patterns[0]})
        seats.add(member["role"])
        ids.add(member["obligation_id"])
    return members


def validate_specialists(runtime, unit, primary_roles) -> list[str]:
    try:
        _members(runtime.config, unit, primary_roles)
    except ValueError as exc:
        return [str(exc)]
    return []


class _MissingContractField(ValueError):
    pass


def _contract_slice(runtime, member, descriptor):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    require_artifact_access(descriptor.get("access_scope"), actor=member["role"],
                            role=member["role"], purpose="task-verify")
    body = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    fields = [ref.removeprefix("contract:$.") for ref in member["input_refs"] if ref.startswith("contract:$.")]
    if missing := [field for field in fields if field not in body]:
        raise _MissingContractField(f"specialist contract fields absent on the bound task: {', '.join(missing)}")
    if "task-contract" in member["input_refs"] or "contract" in member["input_refs"]:
        sliced = descriptor
    else:
        sliced = write_immutable_json_sidecar(runtime.state_dir, {
            "contract_sha256": descriptor["sha256"], "selected": {key: body[key] for key in fields},
        }, root="task-verification/inputs", kind="task_contract_slice",
            schema_version="task-contract-slice.v1", created_by="kernel")
    return {**sliced, "source_id": "specialist-contract", "artifact_id": "specialist-contract",
            "allowed_paths": ["$"]}


def validate_invocation(runtime, event):
    """Recheck the durable grant when an invocation is consumed after a restart."""
    from zf.runtime.task_contract_snapshot import effective_contract_revision
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts

    p = event.payload
    unit = p["verify_specialist_obligation"]
    events = runtime.event_log.read_all()
    op = reduce_workflow_operations(events).get(p.get("workflow_operation_id")) or {}
    task = runtime.task_store.get(event.task_id)
    generation = task_pipeline_generation_contexts(events).get(event.task_id, {})
    if (event.origin != "kernel" or event.actor != "orchestrator" or op.get("operation_type") != "task_verify_specialist"
            or not task or not isinstance(unit, dict) or effective_contract_revision(task) != unit.get("contract_revision")
            or generation.get("generation_admitted_event_id") != event.causation_id
            or generation.get("task_map_generation") != unit.get("task_map_generation")
            or generation.get("workflow_run_id") != p.get("workflow_run_id")):
        raise ValueError("specialist invocation lacks a current task-scoped grant")
    from zf.runtime.orchestrator_agent_run_plan import current_run_plan_admission

    plan = current_run_plan_admission(events, workflow_run_id=p["workflow_run_id"],
        plan_artifact_package_ref=generation.get("plan_artifact_package_ref", ""),
        plan_artifact_package_digest=generation.get("plan_artifact_package_digest", ""),
        task_map_generation=generation["task_map_generation"], verify_task=event.task_id, require_kernel=True)
    current_target = runtime._task_ref_entry(event.task_id).get("source_commit")
    if plan.get("execution_plan_ref") != unit.get("execution_plan_ref") or (
            current_target and current_target != unit.get("target_commit")):
        raise ValueError("specialist invocation was superseded by a new assignment or target")
    request = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
    if p.get("artifact_refs") != [request["contract_ref"]]:
        raise ValueError("specialist invocation inputs differ from the admitted task slice")
    return request["contract_ref"]


def _opinion(state_dir, events, op, obligation, signer):
    """Only an admitted child of this exact specialist invocation can supply its opinion."""
    if op.get("status") != "settled" or not op.get("admitted_call_result_ref"):
        return None
    children = {row["operation_id"]: row for row in reduce_workflow_operations(events).values()
                if row.get("parent_operation_id") == op["operation_id"]
                and row.get("role_instance") == obligation["role"] and row["status"] == "settled"}
    for event in reversed(events):
        p = event.payload
        if event.type != "workflow.call.result.admitted" or event.origin != "kernel":
            continue
        child = children.get(p.get("operation_id"))
        if (not child or not child.get("admitted_call_result_ref") or p.get("request_hash") != child["request_hash"]
                or p.get("envelope_ref") != child["admitted_call_result_ref"]):
            continue
        descriptor = p.get("control_result_ref")
        if not descriptor or p.get("semantic_verdict") not in {"passed", "approved", "rejected"}:
            continue
        envelope = hydrate_sidecar_ref(state_dir, child["admitted_call_result_ref"]).payload
        if envelope.get("execution", {}).get("status") != "completed":
            continue
        require_artifact_access(descriptor.get("access_scope"), actor=signer.instance_id,
                                role=signer.name, purpose="task-verify")
        hydrate_sidecar_ref(state_dir, descriptor)
        source = "specialist-" + obligation["obligation_id"]
        return {**descriptor, "source_id": source, "artifact_id": source, "allowed_paths": ["$"]}, event
    return None


def _reuse_opinion(runtime, events, op, obligation, signer, run):
    from zf.runtime.stage_definition_binding import restore_stage_definition

    if not op or op["operation_type"] != "task_verify_specialist" or op["workflow_run_id"] != run:
        raise ValueError("specialist reuse source is not a bound operation of this Run")
    prior = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
    previous = prior["verify_specialist_obligation"]
    rebinding_keys = {"work_unit_id", "execution_plan_ref", "reuse_operation_id"}
    if any(previous.get(key) != obligation.get(key) for key in (previous.keys() | obligation.keys()) - rebinding_keys):
        raise ValueError("specialist reuse differs in its contract, scope, role or target")
    restore_stage_definition(runtime.state_dir, runtime.config, prior["stage_definition_ref"])
    receipt = _opinion(runtime.state_dir, events, op, previous, signer)
    if not receipt:
        raise ValueError("specialist reuse requires a completed, independently admitted opinion")
    source, admission = receipt
    identity = {key: obligation[key] for key in ("task_id", "task_map_generation", "contract_revision",
                "target_commit", "work_unit_id", "obligation_id", "role", "execution_plan_ref")}
    body = {**identity, "workflow_run_id": run, "source_operation_id": op["operation_id"],
            "source_result_event_id": admission.id, "evidence_ref": admission.payload["control_result_ref"]}
    if not any(e.type == "task.verify.specialist.reused" and e.origin == "kernel" and e.payload == body for e in events):
        runtime.event_writer.append(ZfEvent(type="task.verify.specialist.reused", actor="orchestrator", origin="kernel",
            task_id=identity["task_id"], correlation_id=run, causation_id=admission.id, payload=body))
    return source


def specialist_inputs(runtime: Any, assignment: Mapping, *, context: Mapping, task_id: str,
                      target_commit: str, signer: Any, contract_ref: dict, generation: int) -> list[dict]:
    from zf.runtime.call_result_runtime import workflow_operation_service
    from zf.runtime.stage_definition_binding import pin_stage_definition
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting

    members = _members(runtime.config, assignment, [signer.instance_id, *assignment["preferred_roles"]])
    run = context["workflow_run_id"]
    evidence, pending, incidents, available = [], [], [], []
    for member in members:
        obligation = {**member, "task_id": task_id, "task_map_generation": context["task_map_generation"],
                      "target_commit": target_commit, "contract_revision": assignment["contract_revision"],
                      "work_unit_id": assignment["work_unit_id"], "execution_plan_ref": assignment["execution_plan_ref"]}
        key = operation_request_hash({"obligation": obligation, "operation_generation": generation})
        op_id = stable_operation_id(workflow_run_id=run, parent_stage_id=member["pattern_id"],
                                    operation_key=key, operation_type="task_verify_specialist")
        with locked_path(Path(runtime.state_dir) / "locks" / op_id):
            events = runtime.event_log.read_all()
            operations = reduce_workflow_operations(events)
            if member.get("reuse_operation_id"):
                prior_id = member["reuse_operation_id"]
                try:
                    evidence.append(_reuse_opinion(runtime, events, operations.get(prior_id), obligation, signer, run))
                    available.append({"obligation_id": member["obligation_id"], "operation_id": prior_id})
                except (KeyError, OSError, ValueError) as exc:
                    incidents.append({"operation_id": prior_id, "reason": str(exc),
                                      "source_event_id": context["generation_admitted_event_id"]})
                continue
            op = operations.get(op_id)
            if op:
                receipt = _opinion(runtime.state_dir, events, op, obligation, signer)
                if receipt:
                    evidence.append(receipt[0])
                    available.append({"obligation_id": member["obligation_id"], "operation_id": op_id})
                    continue
            trigger = next((e for e in events if e.origin == "kernel" and e.type == "workflow.invoke.requested"
                            and e.payload.get("workflow_operation_id") == op_id), None)
            rejection = next((e for e in reversed(events) if trigger and e.type == "workflow.invoke.rejected"
                              and e.origin in {"", "kernel"} and e.actor == "zf-cli" and e.causation_id == trigger.id), None)
            if rejection or (op and op["status"] in {"settled", "failed", "cancelled", "blocked", "superseded"}):
                source = rejection or next(e for e in events if e.id == op["last_event_id"])
                incidents.append({"operation_id": op_id, "source_event_id": source.id,
                                  "reason": source.payload.get("reason") or op["status"]})
                continue
            if not trigger:
                missing = ""
                try:
                    source = _contract_slice(runtime, member, contract_ref)
                except _MissingContractField as exc:
                    missing = str(exc)
                    source = {"sha256": contract_ref["sha256"]}
                request = {"workflow_run_id": run, "task_id": task_id, "pattern_id": member["pattern_id"],
                           "target_ref": target_commit, "verify_specialist_obligation": obligation,
                           "contract_ref": source, "stage_definition_ref": pin_stage_definition(
                               runtime.state_dir, runtime.config, member["pattern_id"], project_root=runtime.project_root)}
                prepared = workflow_operation_service(runtime).ensure_operation(
                    workflow_run_id=run, operation_id=op_id, operation_type="task_verify_specialist",
                    request=request, parent_stage_id=member["pattern_id"], task_id=task_id,
                    causation_id=context["generation_admitted_event_id"], correlation_id=run)
                if prepared.status == "divergent":
                    raise TaskPipelineWaiting("specialist_binding_changed", op_id)
                if missing:
                    blocked = workflow_operation_service(runtime).block(operation_id=op_id, request_hash=prepared.request_hash,
                        workflow_run_id=run, task_id=task_id, reason=missing, causation_id=context["generation_admitted_event_id"],
                        correlation_id=run, details={"failure_class": "specialist_contract_selection_absent",
                                                   "contract_digest": contract_ref["sha256"]})
                    incidents.append({"operation_id": op_id, "source_event_id": blocked.id, "reason": missing})
                    continue
                runtime.event_writer.append(ZfEvent(type="workflow.invoke.requested", actor="orchestrator",
                    origin="kernel", task_id=task_id, correlation_id=run, causation_id=context["generation_admitted_event_id"],
                    payload={"workflow_operation_id": op_id, "workflow_operation_request_hash": prepared.request_hash,
                             "workflow_run_id": run, "task_id": task_id, "pattern_id": member["pattern_id"],
                             "durable_operation": True, "result_protocol_mode": "blocking",
                             "task_map_generation": context["task_map_generation"], "target_ref": target_commit,
                             "artifact_refs": [source], "verify_specialist_obligation": obligation,
                             "expected_output": member["expected_output"]}))
            pending.append(f"{member['obligation_id']} ({member['role']}, {op_id})")
    if incidents:
        from zf.runtime.oa_task_verify_assignment import request_specialist_correction

        # One correction owns this Task snapshot; include every known failing and reusable member.
        incident = {**incidents[0], "prior_execution_plan_ref": assignment["execution_plan_ref"],
                    "available_opinions": available, "affected_operations": incidents}
        request_specialist_correction(runtime, task_id=task_id, context=context, incident=incident,
                                      events=runtime.event_log.read_all())
        raise TaskPipelineWaiting("waiting_for_oa_specialist_correction", incident["source_event_id"])
    if pending:
        raise TaskPipelineWaiting("waiting_for_specialist_evidence", ", ".join(pending))
    return evidence
