"""Compile a bound OA verification recovery without changing historical verdicts."""
from collections.abc import Mapping
from types import SimpleNamespace

from zf.core.events import EventLog, ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.orchestrator_agent_execution_policy import bounded_actions
from zf.runtime.run_contract import stable_json_sha256


def recovery_payload(runtime, *, event, decision, directive, expected, candidate) -> dict:
    recovery = directive.get("candidate_verification_recovery")
    run = decision["identity"]["workflow_run_id"]
    if (decision.get("decision") != "rebind" or directive.get("action") != "rebind"
            or expected.get("candidate_failure_scope") != "aggregate"
            or not isinstance(recovery, Mapping)
            or recovery.get("classification") != "verification_recovery"
            or recovery.get("contract_changed") is not False
            or type(recovery.get("max_replays")) is not int or recovery.get("max_replays") != 1
            or not str(recovery.get("rationale") or "").strip()
            or recovery.get("failure_event_id") != expected["candidate_failure_id"]
            or recovery.get("candidate_snapshot_event_id") != candidate["candidate_event_id"]
            or directive.get("invalidate_refs") or directive.get("input_task_ids")):
        raise ValueError("candidate verification recovery requires one unchanged aggregate directive")
    events = runtime.event_log.read_all()
    from zf.runtime.orchestrator_agent_policy import checkpoint_policy, orchestration_flow_kind
    flow = next((e.payload.get("flow_kind") for e in reversed(events)
        if e.type == "plan.artifact_package.admitted"
        and e.payload.get("workflow_run_id") == run), "") or orchestration_flow_kind(event)
    if (checkpoint_policy(runtime.config, "semantic_failure", flow_kind=flow) != "blocking"
            or "rebind" not in (bounded_actions(runtime.config, flow) or [])):
        raise ValueError("candidate verification recovery requires explicit rebind authority")
    bound = aggregate_candidate_binding(runtime.state_dir, events,
        failure_id=expected["candidate_failure_id"], run_id=run, task_id=expected["target_task_id"])
    members = [*bound["members"], *bound.get("unresolved_members", [])]
    from collections import Counter
    from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin

    stage = next((s for s in runtime.config.workflow.stages if s.id == bound["stage_id"]), None)
    if stage is None or stage.trigger != "candidate.ready" or stage.topology != "fanout_reader":
        raise ValueError("candidate verification stage unavailable")
    if stage.children or stage.assignment.strategy != "static_index":
        raise ValueError("candidate verification recovery requires a static role roster")
    configured_roles = FanoutEvidenceQueriesMixin._fanout_roles(runtime, stage.roles)
    observed_roles = [row["role_instance"] for row in [*members, *bound["completed_members"]]]
    if Counter(role.instance_id for role in configured_roles) != Counter(observed_roles):
        raise ValueError("candidate verification stage membership changed")
    reviews = recovery.get("member_reviews")
    if not isinstance(reviews, list) or len(reviews) != len(members):
        raise ValueError("candidate verification recovery must account for every failed member")
    by_child = {row.get("child_id"): row for row in reviews if isinstance(row, dict)}
    if len(by_child) != len(members):
        raise ValueError("candidate verification recovery contains duplicate or invalid members")
    for member in members:
        row = by_child.get(member["child_id"], {})
        # Admitted reports are identified by operation and immutable control ref.
        # For a result-less timeout the terminal child event is the only evidence.
        require_child_event = not member.get("control_result_ref") or row.get("child_event_id") is not None
        if (row.get("operation_id") != member.get("operation_id")
                or row.get("control_result_ref") != member.get("control_result_ref")
                or (require_child_event and row.get("child_event_id") != member["child_event_id"])
                or row.get("disposition") != "reverify"
                or not str(row.get("rationale") or "").strip()):
            raise ValueError("candidate verification recovery member binding incomplete")
    key = stable_json_sha256({"run": run, "candidate": bound["candidate_event_id"],
        "generation": bound["task_map_generation"], "stage": bound["stage_id"]})
    if any(e.origin == "kernel" and e.type == "candidate.ready"
           and e.payload.get("candidate_verification_recovery_key") == key for e in events):
        raise ValueError("candidate verification recovery already consumed for this frozen input")
    return {
        "action": "stage-replan-new-generation", "workflow_run_id": run,
        "repaired_failure_event_id": expected["candidate_failure_id"],
        "approval_ref": "oa-decision:" + event.id, "reason": recovery["rationale"],
        "orchestrator_decision_event_id": event.id, "directive_id": directive["directive_id"],
        "candidate_verification_recovery_key": key,
        "operator_feedback": ["OA verification recovery preserves every historical finding; "
                              "independently re-evaluate the same frozen acceptance contract.",
                              *[f"{row['child_id']}: {row['rationale']}" for row in reviews]],
    }


def validate_recovery(state_dir, config, events, payload) -> dict:
    """Recheck admitted Agent authority at the controlled action boundary."""
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
    from zf.runtime.orchestrator_agent_admission import admit_orchestrator_agent_decision
    from zf.runtime.orchestrator_agent_decision_apply import (
        _operation_checkpoint_context, _operation_source_descriptors, _directive_ref_rejection, _target_rejection,
    )
    from zf.runtime.run_continuation import human_decisions_block_action
    from zf.runtime.run_manager import _pending_human_decisions

    source = next((e for e in events if e.id == payload.get("orchestrator_decision_event_id")), None)
    if source is None or source.type != "orchestrator.semantic.decision.submitted":
        raise ValueError("candidate recovery lacks its submitted OA decision")
    source = hydrate_profiled_control_result_event(state_dir, source)
    runtime = SimpleNamespace(state_dir=state_dir, config=config, event_log=EventLog(state_dir / "events.jsonl"),
                              task_store=TaskStore(state_dir / "kanban.json"))
    admission = admit_orchestrator_agent_decision(runtime, source)
    if (not admission.admitted or admission.checkpoint != "semantic_failure"
            or admission.checkpoint_policy != "blocking"):
        raise ValueError("candidate recovery OA authority rejected: " + admission.reason)
    if not any(e.origin == "kernel" and e.type == "orchestrator.semantic.decision.admitted"
               and e.causation_id == source.id and e.payload.get("decision_ref") == admission.decision_ref
               and e.payload.get("decision") == "rebind" for e in events):
        raise ValueError("candidate recovery OA admission missing")
    from zf.core.config.render import redact_config, renderable_config_to_primitive
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import load_workflow_operation

    op = load_workflow_operation(runtime.event_log, admission.operation_id)
    request = hydrate_sidecar_ref(state_dir, op["request_ref"]).payload["request"]
    frozen_config = hydrate_sidecar_ref(state_dir, request["effective_config_ref"]).payload["config"]
    if frozen_config != redact_config(renderable_config_to_primitive(config)):
        raise ValueError("candidate verification recovery configuration changed after review")
    expected = _operation_checkpoint_context(runtime, {"operation_id": admission.operation_id})
    decision = admission.decision
    directives = decision.get("delta", {}).get("directives", [])
    if len(directives) != 1 or directives[0].get("directive_id") != payload.get("directive_id"):
        raise ValueError("candidate recovery requires one exact directive")
    directive = directives[0]
    candidate = aggregate_candidate_binding(state_dir, events, failure_id=expected["candidate_failure_id"],
        run_id=decision["identity"]["workflow_run_id"], task_id=expected["target_task_id"])
    rejection = _target_rejection(runtime, directive["target"], expected, action="rebind", candidate=candidate)
    rejection = rejection or _directive_ref_rejection(directive,
        _operation_source_descriptors(runtime, {"operation_id": admission.operation_id}))
    if rejection:
        raise ValueError(rejection)
    approved = recovery_payload(runtime, event=source, decision=decision, directive=directive,
                                expected=expected, candidate=candidate)
    if payload != approved:
        raise ValueError("candidate recovery payload differs from admitted directive")
    if human_decisions_block_action(_pending_human_decisions(events), approved, events=events):
        raise ValueError("candidate recovery awaits the existing human gate")
    return approved


def apply_verification_recovery(runtime, *, event, decision, directive, expected, candidate) -> dict:
    from zf.runtime.control_actions import ControlledActionService

    payload = recovery_payload(runtime, event=event, decision=decision, directive=directive,
                               expected=expected, candidate=candidate)
    request = runtime.event_writer.append(ZfEvent(type="control.action.requested", actor="zf-cli", origin="kernel",
        causation_id=event.id, correlation_id=decision["identity"]["workflow_run_id"], payload=payload))
    service = ControlledActionService(runtime.state_dir, runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, actor="zf-cli", source="orchestrator-agent", surface="runtime")
    return service.execute(action="stage-replan-new-generation", requested_action="stage-replan-new-generation",
                            payload=payload, requested=request)


def published_verification_recovery(runtime, event) -> dict:
    """Acknowledge an already published effect after a crash; never replay it."""
    events = runtime.event_log.read_all()
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event

    original = next((e for e in events if e.id == event.id
        and e.type == "orchestrator.semantic.decision.submitted"), None)
    if original is None:
        return {}
    try:
        original = hydrate_profiled_control_result_event(runtime.state_dir, original)
    except (OSError, ValueError, KeyError):
        return {}
    if original.payload != event.payload:
        return {}
    admission = next((e for e in events if e.origin == "kernel"
        and e.type == "orchestrator.semantic.decision.admitted" and e.causation_id == event.id
        and e.payload.get("decision") == "rebind" and e.payload.get("checkpoint_policy") == "blocking"), None)
    if admission is None:
        return {}
    for request in events[events.index(admission) + 1:]:
        p = request.payload
        if (request.origin != "kernel" or request.type != "control.action.requested"
                or request.causation_id != event.id or p.get("orchestrator_decision_event_id") != event.id
                or p.get("action") != "stage-replan-new-generation"
                or p.get("approval_ref") != "oa-decision:" + event.id
                or not p.get("candidate_verification_recovery_key")):
            continue
        starts = [e for e in events[events.index(request) + 1:] if e.origin == "kernel"
            and e.type == "runtime.action.attempt.started" and e.causation_id == request.id
            and e.payload.get("action") == p["action"] and e.payload.get("payload") == p]
        if len(starts) != 1:
            continue
        triggers = [e for e in events[events.index(starts[0]) + 1:] if e.origin == "kernel"
            and e.type == "candidate.ready" and e.causation_id == request.id
            and e.payload.get("candidate_verification_recovery_key") == p["candidate_verification_recovery_key"]
            and e.payload.get("repaired_failure_event_id") == p.get("repaired_failure_event_id")
            and e.payload.get("workflow_run_id") == admission.payload.get("workflow_run_id")]
        if len(triggers) == 1:
            native = next((e for e in events if e.id == triggers[0].payload.get("candidate_snapshot_event_id")
                and e.origin == "kernel" and e.type == "candidate.ready"
                and e.payload.get("schema_version") == "candidate-freeze-receipt.v1"), None)
            trigger = triggers[0].payload
            mutable = {"schema_version", "rework_of", "rework_attempt", "rework_feedback", "reason"}
            if native is None or any(trigger.get(k) != v for k, v in native.payload.items()
                                     if k not in mutable):
                continue
            if (trigger.get("stage_replan_generation") != "repair-" + request.id
                    or trigger.get("stage_replan_generation_source_event_id") != p["repaired_failure_event_id"]
                    or trigger.get("operator_authorization_ref") != p["approval_ref"]
                    or trigger.get("orchestrator_decision_event_id") != event.id
                    or trigger.get("operator_feedback") != p["operator_feedback"]
                    or trigger.get("repair_reason") != p["reason"]):
                continue
            return {**admission.payload, "applied": True, "status": "applied", "publication_replayed": True,
                    "candidate_verification_recovery_event_id": triggers[0].id}
    return {}
