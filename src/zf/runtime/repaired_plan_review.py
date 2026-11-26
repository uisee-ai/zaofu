"""Resume the first Critic when a previously failed Plan producer has delivered its output."""
from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.core.task.store import TaskStore
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.fanout_identity import fanout_current_status
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.reader_rebuild_admission import _settled_execution_failure, reader_rebuild_dispatch_matches
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_continuation import bind_action_dependencies, blocking_decisions_for_action
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations

KIND = "repaired_plan_first_review"


def _replacement(state, events, by_id, ops, original, completed):
    """An admitted success must descend through applied repairs of this exact failed child."""
    p = completed.payload
    op = ops.get(p.get("operation_id"), {})
    admitted = by_id.get(op.get("call_result_admitted_event_id"))
    if (completed.type != "fanout.child.completed" or completed.origin != "kernel"
            or p.get("result_semantics") != "artifact_production" or p.get("semantic_verdict") != "passed"
            or op.get("status") != "settled" or op.get("operation_type") != "fanout_reader_child"
            or op.get("attempt_domain") != "plan" or not admitted or admitted.origin != "kernel"
            or admitted.type != "workflow.call.result.admitted" or admitted.payload.get("semantic_verdict") != "passed"
            or admitted.payload.get("source_event_id") != completed.causation_id
            or any(not p.get(k) or admitted.payload.get(k) != p[k] for k in
                   ("operation_id", "request_hash", "workflow_run_id", "attempt_id", "control_result_ref"))
            or op.get("admitted_control_result_ref") != p["control_result_ref"]
            or op.get("active_attempt_id") != p["attempt_id"] or op.get("request_hash") != p["request_hash"]):
        return False
    subject = hydrate_sidecar_ref(state, p["control_result_ref"]).payload
    if (subject.get("execution_status") != "completed" or subject.get("verdict") != "passed"
            or any(not p.get(k) or subject.get(k) != p[k] for k in (
                "operation_id", "request_hash", "workflow_run_id", "attempt_id", "child_id",
                "fanout_id", "role_instance", "stage_id", "task_id", "result_semantics"))):
        return False
    for dispatch in reversed(events):
        if (dispatch.type != "fanout.child.dispatched" or dispatch.payload.get("operation_id") != op["operation_id"]
                or dispatch.payload.get("run_id") != op["active_attempt_id"]):
            continue
        prior_id = op.get("parent_operation_id")
        req = next((r for r in events if r.type == "repair.action.requested" and r.origin == "kernel"
                    and r.payload.get("recovery_action") == "child-rebuild"
                    and r.payload.get("target_operation_id") == prior_id
                    and reader_rebuild_dispatch_matches(dispatch, r.payload)), None)
        if not req or not any(e.type == "repair.action.applied" and e.origin == "kernel"
                and e.causation_id == req.id and e.payload.get("action_id") == req.payload.get("action_id")
                for e in events):
            return False
        prior = by_id.get(req.payload.get("source_event_id"))
        op = ops.get(prior_id, {})
        if (not prior or prior.type != "fanout.child.failed" or prior.origin != "kernel" or not op
                or any(prior.payload.get(k) != p[k] for k in ("fanout_id", "child_id", "workflow_run_id", "task_id"))
                or not _settled_execution_failure(state, events, op, prior)):
            return False
        if prior.id == original.id:
            return True
    return False


def _review_inputs(state, events, manifest):
    from zf.runtime.diagnosis_critic_handoff import synth_call_issued
    from zf.runtime.run_manager import _pending_human_decisions

    fanout = manifest["fanout_id"]
    run = manifest.get("workflow_run_id") or manifest["trace_id"]
    by_id = {e.id: e for e in events}
    failure = by_id.get(manifest["synth"].get("last_event_id"))
    aggregate = by_id.get(manifest["aggregate"].get("recovered_from_event_id"))
    active = build_run_admission_projection(events).runs.get(run)
    scoped, = bind_action_dependencies([{"workflow_run_id": run, "task_id": manifest.get("task_id")}],
                                       TaskStore(state / "kanban.json").list_all())
    current_fanout = fanout_current_status(events, fanout)
    if (not current_fanout.known or not current_fanout.current or not failure or not aggregate or failure.type != "fanout.synth.completed"
            or aggregate.type != "fanout.aggregate.completed" or aggregate.causation_id != failure.id
            or aggregate.payload.get("synth_event_id") != failure.id
            or failure.causation_id != manifest["trigger_event_id"]
            or any(e.origin != "kernel" or e.actor != "zf-cli" or e.correlation_id != run
                   or e.payload.get("stage_id") != manifest["stage_id"] or e.payload.get("fanout_id") != fanout
                   or e.payload.get("status") != "failed" or e.payload.get("failure_class") != "reader_execution_failure"
                   for e in (failure, aggregate))
            or any(failure.payload.get(k) for k in ("operation_id", "request_hash", "control_result_ref"))
            or len([e for e in events if e.type == "fanout.synth.completed" and e.payload.get("fanout_id") == fanout]) != 1
            or not active or active.status != "running" or is_dispatch_paused(state, events=events)
            or blocking_decisions_for_action(_pending_human_decisions(events), scoped, events=events)
            or synth_call_issued(state, events, fanout, run, manifest["stage_id"])):
        return None
    before = FanoutManifestProjector(state).rebuild(fanout, events[:events.index(failure)])
    previous = {c["child_id"]: c for c in before["children"]}
    current = {c["child_id"]: c for c in manifest["children"]}
    if previous.keys() != current.keys() or any(c["status"] != "completed" for c in current.values()):
        return None
    fixed = {}
    ops = reduce_workflow_operations(events, workflow_run_id=run)
    for key, old in previous.items():
        child = current[key]
        if old["status"] == "completed" and old["last_event_id"] == child["last_event_id"]:
            continue
        if (old["status"] != "failed" or not _replacement(state, events, by_id, ops,
                by_id[old["last_event_id"]], by_id[child["last_event_id"]])):
            return None
        fixed[old["last_event_id"]] = child["last_event_id"]
    return fixed or None


def open_first_review(runtime, manifest) -> bool:
    """Called by the normal live/cold barrier; previous Critic decisions never take this route."""
    aggregate = manifest.get("aggregate") or {}
    if ((manifest.get("synth") or {}).get("status") != "failed" or aggregate.get("status") != "pending"
            or not aggregate.get("recovered_from_event_id") or not (manifest.get("workflow_run_id") or manifest.get("trace_id"))):
        return False
    with locked_path(runtime.state_dir / "locks" / "reader-stage-recovery"):
        events = runtime.event_log.read_all()
        projector = FanoutManifestProjector(runtime.state_dir)
        current = projector.rebuild(manifest["fanout_id"], events)
        if current.get("aggregate") != aggregate or current.get("synth") != manifest.get("synth"):
            return False
        manifest = current
        failure = manifest["synth"]["last_event_id"]
        if any(e.type == "fanout.aggregate.started" and e.payload.get("recovery_kind") == KIND
               and e.payload.get("prior_synth_event_id") == failure for e in events):
            return False
        try:
            replacements = _review_inputs(runtime.state_dir, events, manifest)
        except (KeyError, ValueError, OSError, TypeError):
            return False
        if not replacements:
            return False
        run = manifest.get("workflow_run_id") or manifest["trace_id"]
        runtime.event_writer.append(ZfEvent(type="fanout.aggregate.started", actor="zf-cli", origin="kernel",
            causation_id=next(e.id for e in reversed(events) if e.id in replacements.values()),
            correlation_id=run, task_id=manifest.get("task_id") or None,
            payload={"recovery_kind": KIND, "workflow_run_id": run,
                     "fanout_id": manifest["fanout_id"], "stage_id": manifest["stage_id"],
                     "mode": manifest["aggregate_config"]["mode"], "prior_synth_event_id": failure,
                     "original_aggregate_event_id": aggregate["recovered_from_event_id"],
                     "recovered_child_terminals": replacements}))
        projector.write_manifest(manifest["fanout_id"], runtime.event_log.read_all())
        return True
