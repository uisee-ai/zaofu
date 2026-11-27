"""Bind duplicate Plan index entries to the completed owner action and reviewed inputs."""

from zf.runtime.plan_synth_recovery_refs import bound_critic_candidate_refs
from zf.runtime.run_scope import run_aliases
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_replan_generation import completed_generation_source, escalation_generation_id


def _owner_request(event, run, authorization):
    if (not event or event.type != "web.action.requested" or event.actor != "web"
            or event.origin not in {"", "kernel"} or event.payload.get("source_session_id")
            or event.payload.get("action") != "stage-replan-new-generation"):
        return {}
    request = event.payload.get("request")
    if not isinstance(request, dict) or request.get("workflow_run_id") != run or not authorization:
        return {}
    return request if request.get("approval_ref") == authorization else {}


def owner_reviewed_history(state_dir, manifest, body, events):
    """A different route or incomplete prefix keeps the full source mandatory."""
    run = manifest["workflow_run_id"]
    domain = manifest.get("handoff_authority_contract") or {}
    by_id = {event.id: event for event in events}
    fanout = by_id.get(manifest.get("source_event_id"))
    if (not fanout or fanout.type != "fanout.started" or fanout.origin != "kernel"
            or fanout.payload.get("workflow_run_id") != run
            or fanout.causation_id != fanout.payload.get("trigger_event_id")
            or domain.get("attempt_domain") != "plan" or domain.get("operation_type") != "fanout_reader_child"
            or domain.get("continuation_key") != body["rework_of"]
            or domain.get("stage_id") != fanout.payload.get("stage_id")):
        return False
    trigger = by_id.get(fanout.causation_id)
    fields = ("rework_of", "rework_attempt", "rework_feedback", "operator_feedback", "previous_plan_candidate_refs")
    if (not trigger or trigger.payload.get("workflow_run_id") != run
            or trigger.payload.get("stage_id") != fanout.payload.get("stage_id")
            or any(trigger.payload.get(key) != body[key] for key in fields)):
        return False
    authorization = trigger.payload.get("operator_authorization_ref")
    if "redrive_of" in trigger.payload:
        original = by_id.get(trigger.payload["redrive_of"])
        request = _owner_request(by_id.get(trigger.causation_id), run, authorization)
        if (not original or trigger.actor != "web" or trigger.origin != "kernel"
                or trigger.type != original.type or request.get("redrive_owner_trigger_event_id") != original.id
                or request.get("escalation_event_id") != original.payload.get("stage_replan_generation_source_event_id")
                or trigger.payload != {**original.payload, "redrive_of": original.id, "rework_of": original.id}):
            return False
        trigger = original

    owner = by_id.get(trigger.causation_id)
    request = _owner_request(owner, run, authorization)
    escalation = request.get("escalation_event_id")
    if (not escalation or trigger.actor != "zf-cli" or trigger.origin not in {"", "kernel"}
            or trigger.payload.get("stage_replan_generation") != escalation_generation_id(run, escalation)
            or request.get("operator_feedback") != body["operator_feedback"]):
        return False
    receipts = [e for e in events if e.type == "runtime.action.completed" and e.actor == "web"
                and e.origin in {"", "kernel"} and e.causation_id == trigger.id
                and e.payload.get("stage_trigger_event_id") == trigger.id and not e.payload.get("replayed")]
    if len(receipts) != 1:
        return False
    receipt = receipts[0]
    source = completed_generation_source(receipt, by_id, run_aliases(events))
    goal = by_id.get(receipt.payload.get("run_goal_update_event_id"))
    failure = by_id.get(receipt.payload.get("source_failure_event_id"))
    ready = by_id.get(trigger.payload.get("rework_of"))
    if (not source or source.id != escalation or not goal or goal.causation_id != owner.id
            or not failure or failure.id != source.payload.get("source_event_id") or failure.origin != "kernel"
            or not ready or ready.type != "task_map.ready" or ready.origin != "kernel"
            or ready.payload.get("workflow_run_id") != run or failure.causation_id != ready.id
            or failure.payload.get("trigger_event_id") != ready.id):
        return False
    synth = by_id.get(ready.causation_id)
    if (not synth or synth.type != "fanout.synth.completed" or synth.payload.get("workflow_run_id") != run
            or not all(synth.payload.get(k) and synth.payload[k] == ready.payload.get(k)
                       for k in ("stage_id", "fanout_id"))):
        return False
    admitted = [e for e in events if e.type == "workflow.call.result.admitted" and e.origin == "kernel"
                and e.payload.get("workflow_run_id") == run and e.payload.get("source_event_id") == synth.id
                and e.payload.get("admission_status") == "admitted" and e.payload.get("semantic_verdict") == "passed"
                and all(e.payload.get(k) and e.payload[k] == synth.payload.get(k)
                        for k in ("operation_id", "request_hash", "control_result_ref"))]
    if len(admitted) != 1:
        return False
    control = hydrate_sidecar_ref(state_dir, admitted[0].payload["control_result_ref"]).payload
    return (isinstance(control, dict) and control.get("verdict") == "passed"
            and control.get("execution_status") == "completed"
            and bound_critic_candidate_refs(state_dir, events, {**synth.payload, **control})
            == body["previous_plan_candidate_refs"])
