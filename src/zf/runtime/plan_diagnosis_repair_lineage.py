"""Resolve the Candidate behind a controlled, fixed-input Planner repair."""
from collections import defaultdict

from zf.runtime.candidate_freeze_recovery_identity import superseding_reader_freeze
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.reader_failure_repair import repaired_failure_kind
from zf.runtime.stage_failure_replan import STAGE_REPLAN_CAP
from zf.runtime.stage_replan_generation import escalation_generation_id


_IDENTITY = ("pdd_id", "resume_scope", "replan_classification", "rework_summary",
             "candidate_event_id", "source_commit", "target_ref", "task_map_generation", "plan_artifact_package_digest",
             "orchestration_delta_ref", "orchestration_delta_digest", "orchestrator_decision_ref")
_WRITERS = ("input_task_ids", "task_ids", "failed_task_ids", "downstream_task_ids", "changed_task_ids")


def _require(ok):
    if not ok:
        raise ValueError("Candidate diagnosis repair lacks its exact controlled Planner lineage")


def _one(matches):
    _require(len(matches) == 1)
    return matches[0]


def _request(cause):
    if cause and cause.payload.get("action") == "stage-replan-new-generation":
        if cause.type == "web.action.requested" and cause.actor == "web" and cause.origin in {"", "kernel"}:
            return cause.payload.get("request")
        if cause.type == "control.action.requested" and cause.origin == "kernel":
            return cause.payload
    return None


def _bound_start(start, cause, request):
    args = start.payload.get("payload") or {}
    return (start.type == "runtime.action.attempt.started" and start.origin == cause.origin
            and start.actor == cause.actor and start.causation_id == cause.id
            and start.payload.get("attempt_id") and start.payload.get("action") == cause.payload["action"]
            and start.payload.get("surface") == ("web" if cause.type == "web.action.requested" else "runtime")
            and all(args.get(k) == request.get(k) for k in ("approval_ref", "escalation_event_id", "repaired_failure_event_id")))


def _closed_synth_transport(state, events, by_id, positions, parent, fanout, synth, repair):
    """Prove a provider-loss terminal belongs to this exact Planner generation."""
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.synth_restart_checkpoint import _same_transport_closeout
    from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations

    loss = by_id.get(synth.causation_id)
    p = synth.payload
    if (p.get("status") != "failed" or p.get("failure_class") != "worker_noop_or_terminal_missing"
            or p.get("reason") != "provider_turn_closed_without_synth_result_recovery_exhausted"
            or p.get("report") or loss is None or loss.type != "fanout.child.dispatch_lost"
            or loss.actor != "zf-cli" or loss.origin != "kernel" or loss.correlation_id != fanout.correlation_id
            or loss.payload.get("child_id") != "synth" or loss.payload.get("semantic_attempt_consumed") is not False
            or loss.payload.get("reason") != "provider_turn_closed_without_synth_result"
            or loss.payload.get("lost_signal_type") != "provider.turn.closed"
            or loss.payload.get("lost_signal_event_id") != loss.causation_id
            or any(not p.get(k) or loss.payload.get(k) != p[k]
                   for k in ("fanout_id", "stage_id", "run_id", "role_instance"))):
        return False
    signal = by_id.get(loss.causation_id)
    if (signal is None or signal.type != "provider.turn.closed" or signal.actor != p["role_instance"]
            or not positions[fanout.id] < positions[signal.id] < positions[loss.id] < positions[synth.id]):
        return False
    history = events[:positions[repair.id]]
    dispatch = next((e for e in reversed(history) if e.type == "fanout.synth.dispatched"
                     and e.payload.get("fanout_id") == p["fanout_id"]), None)
    if (dispatch is None or dispatch.origin != "kernel" or dispatch.actor != "zf-cli"
            or not positions[fanout.id] < positions[dispatch.id] < positions[signal.id]
            or any(dispatch.payload.get(k) != p[k] for k in ("stage_id", "run_id", "role_instance"))):
        return False
    op = reduce_workflow_operations(history, workflow_run_id=fanout.correlation_id).get(dispatch.payload.get("operation_id"), {})
    if (op.get("status") != "failed" or op.get("operation_type") != "fanout_synth" or op.get("call_result_admitted_event_id")
            or op.get("active_attempt_id") != p["run_id"]
            or not _same_transport_closeout(by_id.get(op.get("last_event_id")), synth, dispatch)):
        return False
    requests = [e for e in history if e.type == "workflow.operation.requested"
                and e.payload.get("operation_id") == op["operation_id"]]
    if len(requests) != 1:
        return False
    issued = requests[0]
    if (issued.origin != "kernel" or issued.actor != "zf-cli" or issued.causation_id != parent.id
            or not positions[fanout.id] < positions[issued.id] < positions[dispatch.id]):
        return False
    signed = hydrate_sidecar_ref(state, op["request_ref"]).payload
    request = signed["request"]
    return (operation_request_hash(signed) == op["request_hash"] == dispatch.payload.get("request_hash")
            and all(request.get(k) == p[k] for k in ("fanout_id", "stage_id", "role_instance"))
            and request.get("active_attempt_id") == p["run_id"] and signed.get("task_id") == (fanout.task_id or ""))


def awaiting_diagnosis_repair_receipt(trigger, state_dir):
    """Defer only while the publisher still holds the OS lock for this request."""
    from zf.core.state.locks import locked_path, lock_path_for
    from zf.runtime.stage_replan_generation import generation_publication_lock

    body = trigger.payload
    if (body.get("resume_scope") != "candidate_diagnosis_only" or not body.get("stage_replan_generation")
            or body.get("operator_authorized") is not True or not trigger.causation_id
            or trigger.causation_id == body.get("rework_of")):
        return False
    lock = generation_publication_lock(state_dir, trigger.causation_id)
    if not lock_path_for(lock).exists():
        return False
    try:
        with locked_path(lock, timeout_seconds=0):
            return False
    except TimeoutError:
        return True


def diagnosis_repair_failure(state, events, payload, context, handoff_source_id, run):
    """Follow only strictly prior, same-carrier failures and registered recovery edges."""
    positions = {e.id: i for i, e in enumerate(events)}
    by_id = {e.id: e for e in events}
    starts, completions = defaultdict(list), defaultdict(list)
    for e in events:
        if e.type == "fanout.started" and e.origin == "kernel":
            starts[e.payload.get("fanout_id")].append(e)
        if e.type == "runtime.action.attempt.completed":
            completions[e.payload.get("result_event_id")].append(e)
    anchor = payload.get("pdd_id")
    repair_claimed = bool(payload.get("stage_replan_generation") or payload.get("repaired_failure_event_id"))
    start = by_id.get(handoff_source_id)
    if start is None and not repair_claimed:
        return context["rework_of"]
    _require(start is not None)
    if start.type == "fanout.child.dispatched" and start.origin == "kernel":
        start = _one(starts[start.payload.get("fanout_id")])
    current = by_id.get(start.causation_id) if start.type == "fanout.started" else start
    if not repair_claimed and not (current and current.payload.get("stage_replan_generation")):
        return context["rework_of"]
    identity = {k: payload.get(k) or context.get(k) for k in _IDENTITY}
    _require(all(identity.values()) and not any(payload.get(k) or context.get(k) for k in _WRITERS))
    if start.type == "fanout.started":
        _require(start.origin == "kernel" and start.actor == "zf-cli" and start.task_id == anchor
                 and start.correlation_id == run and start.causation_id == start.payload.get("trigger_event_id"))
    _require(current and current.payload.get("rework_of") == context.get("rework_of")
             and all(current.payload.get(k) == payload.get(k) for k in (
                 "stage_replan_generation", "stage_replan_generation_source_event_id", "operator_authorization_ref")))
    frozen = by_id.get(identity["candidate_event_id"])
    package = reduce_plan_artifact_packages(events, workflow_run_id=run).get("current") or {}
    _require(frozen and frozen.type == "candidate.ready" and frozen.origin == "kernel"
             and frozen.payload.get("workflow_run_id") == run and frozen.payload.get("freeze_id")
             and frozen.payload.get("schema_version") == "candidate-freeze-receipt.v1"
             and frozen.payload.get("candidate_head_commit") == identity["source_commit"]
             and not superseding_reader_freeze(reversed(events), run, frozen.payload["freeze_id"], identity["source_commit"])
             and package.get("package_digest") == identity["plan_artifact_package_digest"]
             and package.get("task_map_generation") == identity["task_map_generation"])
    while True:
        body = current.payload
        _require(current.actor == "zf-cli" and (body.get("workflow_run_id") or current.correlation_id) == run
                 and all(body.get(k) == v for k, v in identity.items())
                 and not any(body.get(k) for k in _WRITERS))
        if not body.get("stage_replan_generation"):
            _require(current.origin == "kernel" and body.get("rework_source") == "orchestrator.semantic.failure.requested")
            return body["rework_of"]
        failure = by_id.get(body.get("rework_of"))
        cause = by_id.get(current.causation_id)
        _require(failure and failure.origin == "kernel" and failure.actor == "zf-cli"
                 and failure.correlation_id == run and failure.payload.get("pdd_id") == anchor
                 and failure.payload.get("status") == "failed" and cause
                 and positions[failure.id] < positions[current.id])
        fanout = _one(starts[failure.payload.get("fanout_id")])
        parent = by_id.get(fanout.payload.get("trigger_event_id"))
        synth = by_id.get(failure.causation_id)
        _require(parent and fanout.actor == "zf-cli" and fanout.causation_id == parent.id and fanout.task_id == anchor
                 and fanout.payload.get("topology") == "fanout_reader" and fanout.correlation_id == run
                 and fanout.payload.get("stage_id") == failure.payload.get("stage_id")
                 and (fanout.payload.get("aggregate") or {}).get("failure_event") == failure.type
                 and parent.type == current.type and synth and synth.type == "fanout.synth.completed"
                 and synth.origin == "kernel" and synth.actor == "zf-cli"
                 and (synth.causation_id == parent.id or _closed_synth_transport(
                     state, events, by_id, positions, parent, fanout, synth, current))
                 and synth.payload.get("status") == "failed"
                 and synth.payload.get("fanout_id") == fanout.payload.get("fanout_id")
                 and positions[parent.id] < positions[fanout.id] < positions[synth.id] < positions[failure.id]
                 and repaired_failure_kind(state, events, failure) == "infra")
        if cause.id == failure.id:
            # A kernel retry inside an admitted generation can only step forward within its original cap.
            _require(current.origin == "kernel" and type(parent.payload.get("rework_attempt")) is int
                     and body.get("rework_attempt") == parent.payload["rework_attempt"] + 1 <= STAGE_REPLAN_CAP
                     and all(body.get(k) == parent.payload.get(k) for k in (
                         "stage_replan_generation", "stage_replan_generation_source_event_id",
                         "operator_authorization_ref", "repaired_failure_event_id")))
        else:
            request = _request(cause)
            _require(isinstance(request, dict) and request.get("approval_ref")
                     and current.origin in {cause.origin, "kernel"} and body.get("operator_authorized") is True
                     and positions[failure.id] < positions[cause.id] < positions[current.id]
                     and request["approval_ref"] == body.get("operator_authorization_ref")
                     and not any(request.get(k) for k in (*_WRITERS, "repair_target", "resume_candidate_synth_event_id")))
            receipt = _one([e for e in completions[current.id]
                            if e.causation_id in by_id and by_id[e.causation_id].causation_id == cause.id
                            and e.origin == cause.origin and e.actor == cause.actor
                            and e.payload.get("action") == "stage-replan-new-generation" and e.payload.get("status") == "applied"])
            started = by_id.get(receipt.causation_id)
            _require(started and _bound_start(started, cause, request)
                     and positions[cause.id] < positions[started.id] < positions[current.id] < positions[receipt.id]
                     and started.payload.get("surface") == receipt.payload.get("surface")
                     and started.payload["attempt_id"] == receipt.payload.get("attempt_id"))
            if request.get("repaired_failure_event_id"):
                _require(request["repaired_failure_event_id"] == failure.id == body.get("repaired_failure_event_id")
                         == body.get("stage_replan_generation_source_event_id")
                         and body["stage_replan_generation"] == "repair-" + cause.id)
            else:
                escalation = by_id.get(request.get("escalation_event_id"))
                _require(escalation and escalation.type == "human.escalate"
                         and (escalation.origin == "kernel" or (escalation.origin == "" and escalation.actor == "orchestrator"))
                         and escalation.correlation_id == run
                         and escalation.payload.get("failure_class") == "stage_replan_cap_exhausted"
                         and escalation.payload.get("source_event_id") == failure.id
                         and "start_new_generation" in (escalation.payload.get("allowed_actions") or [])
                         and body.get("stage_replan_generation_source_event_id") == escalation.id
                         and body["stage_replan_generation"] == escalation_generation_id(run, escalation.id))
        current = parent
