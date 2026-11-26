"""Resolve writer admission's exact approved Plan and owner-granted rework generation."""
from zf.runtime.plan_synth_recovery_refs import bound_critic_candidate_refs
from zf.runtime.run_scope import event_run_id, run_aliases
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_replan_generation import completed_generation_source


def writer_failure_generation(state, index, events, failure, ready):
    """Legacy writer failures omit generation. Never infer it from the latest Run event."""
    p, r = failure.payload, ready.payload
    generation = r.get("stage_replan_generation")
    if not (generation or p.get("stage_replan_generation")):
        return {}
    aliases = run_aliases(events)
    run = event_run_id(ready, aliases=aliases)
    if (not generation or failure.origin != "kernel" or ready.origin != "kernel"
            or failure.causation_id != ready.id or p.get("trigger_event_id") != ready.id
            or not run or event_run_id(failure, aliases=aliases) != run or p.get("stage_id") != r.get("stage_id")
            or any(p.get(key) and p[key] != r.get(key) for key in
                   ("stage_replan_generation", "stage_replan_generation_source_event_id", "plan_artifact_package_id",
                    "plan_artifact_package_ref", "plan_artifact_package_digest", "task_map_ref", "task_map_digest"))):
        raise ValueError("writer failure differs from its approved Plan generation")
    receipts = [e for i, e in index.rows("runtime.action.completed") if i < index.positions[ready.id]
                and e.origin in {"", "kernel"} and not e.payload.get("replayed")
                and e.payload.get("stage_replan_generation") == generation
                and e.payload.get("run_id") == run and e.payload.get("action") == "stage-replan-new-generation"]
    if len(receipts) != 1 or not completed_generation_source(receipts[0], index.by_id, aliases):
        raise ValueError("writer failure has no applied owner generation")
    marker = index.by_id[receipts[0].payload["stage_trigger_event_id"]]
    keys = ("stage_replan_generation", "stage_replan_generation_source_event_id",
            "operator_authorized", "operator_authorization_ref")
    if any(r.get(key) != marker.payload.get(key) for key in keys):
        raise ValueError("approved Plan does not match its owner generation")
    result = {key: r[key] for key in keys}
    from zf.runtime.unissued_plan_critic_recovery import KIND

    if marker.payload.get("recovery_kind") != KIND or marker.payload.get("fanout_id") != r.get("fanout_id"):
        return result  # Other owner generations retain their established package-based admission rework.
    if (not state or any(i > index.positions[ready.id] and e.origin == "kernel"
                         and event_run_id(e, aliases=aliases) == run and e.payload.get("stage_id") == r.get("stage_id")
                         for i, e in index.ordered(("task_map.ready", "fanout.child.completed")))):
        raise ValueError("the approved Planner checkpoint is unavailable or superseded")
    synth = index.by_id.get(ready.causation_id)
    if (not synth or synth.type != "fanout.synth.completed" or event_run_id(synth, aliases=aliases) != run
            or index.positions[synth.id] <= index.positions[marker.id]
            or any(synth.payload.get(k) != r.get(k) for k in ("fanout_id", "stage_id"))):
        raise ValueError("writer admission has no bound independent Critic result")
    admissions = [e for _, e in index.rows("workflow.call.result.admitted") if e.origin == "kernel"
                  and event_run_id(e, aliases=aliases) == run and e.payload.get("source_event_id") == synth.id
                  and e.payload.get("admission_status") == "admitted" and e.payload.get("semantic_verdict") == "passed"
                  and all(e.payload.get(k) and e.payload[k] == synth.payload.get(k)
                          for k in ("operation_id", "request_hash", "control_result_ref"))]
    if len(admissions) != 1:
        raise ValueError("the current Critic approval was not uniquely admitted")
    control = hydrate_sidecar_ref(state, admissions[0].payload["control_result_ref"]).payload
    if control.get("verdict") != "passed" or control.get("execution_status") != "completed":
        raise ValueError("the current Critic has not approved the Plan")
    refs = bound_critic_candidate_refs(state, events, {**synth.payload, **control})
    checkpoint = hydrate_sidecar_ref(state, marker.payload["review_checkpoint_ref"]).payload
    identity = ("source_id", "ref", "sha256", "artifact_id")
    if not refs or [tuple(r[k] for k in identity) for r in refs] != [
            tuple(r[k] for k in identity) for r in checkpoint["candidate_inputs"]]:
        raise ValueError("the approved candidate differs from the owner's Planner checkpoint")
    return {**result, "previous_plan_candidate_refs": refs}
