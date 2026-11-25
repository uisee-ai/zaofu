"""Request one advisory OA observation while sibling Candidate Readers are active."""

from zf.core.events import ZfEvent
from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.candidate_plan_recovery import candidate_reader_binding
from zf.runtime.orchestrator_agent_operations import request_orchestrator_agent_checkpoint
from zf.runtime.orchestrator_agent_policy import checkpoint_policy, orchestration_flow_kind
from zf.runtime.orchestrator_agent_role import configured_orchestrator_role
from zf.runtime.workflow_operation import reduce_workflow_operations


def replay_partial_candidate_observation(runtime, manifest: dict, events) -> bool:
    """Re-evaluate a persisted child failure once per Reader frontier after process restart."""
    if (manifest.get("aggregate_config") or {}).get("mode", "wait_for_all") != "wait_for_all":
        return False
    failures = tuple(sorted(c["last_event_id"] for c in manifest["children"]
                            if c.get("status") == "failed" and c.get("last_event_id")))
    if not failures or configured_orchestrator_role(runtime) is None:
        return False
    # A new child result or a controller restart checks the durable receipts again.
    # Repeated ticks at the same frontier need no sidecar scan.
    seen = getattr(runtime, "_observed_partial_candidate_failures", {})
    trigger = manifest.get("trigger_event_id")
    key = (manifest.get("fanout_id"), trigger)
    if seen.get(key) == failures:
        return False
    seen[key] = failures
    runtime._observed_partial_candidate_failures = seen
    source = next((e for e in events if e.id == trigger), None)
    if source is None:  # Long-running frozen Candidates may precede the recovery window.
        source = next((e for e in runtime.event_log.read_all() if e.id == trigger), None)
    return bool(source and source.type == "candidate.ready")


def observe_partial_candidate_failure(runtime, fanout_id: str, manifest: dict) -> None:
    """Bind the earliest admitted product counterexample to a shadow-only checkpoint."""
    role = configured_orchestrator_role(runtime)
    if role is None:
        return
    events = runtime.event_log.read_all()
    trigger = next((e for e in events if e.id == manifest.get("trigger_event_id")), None)
    if not trigger or trigger.type != "candidate.ready":
        return
    run = trigger.payload.get("workflow_run_id") or trigger.correlation_id
    if any(e.type in {"fanout.aggregate.completed", "fanout.cancelled"} and e.origin == "kernel"
           and e.payload.get("fanout_id") == fanout_id for e in events):
        return
    package = next((e.payload for e in reversed(events) if e.type == "plan.artifact_package.admitted"
                    and e.origin == "kernel" and (e.payload.get("workflow_run_id") or e.correlation_id) == run), {})
    flow = package.get("flow_kind") or orchestration_flow_kind(trigger)
    if not package or not checkpoint_policy(runtime.config, "semantic_failure", flow_kind=flow):
        return
    failures = [e for e in events if e.type == "fanout.child.failed" and e.origin == "kernel"
                and e.payload.get("fanout_id") == fanout_id]
    ids = {e.id for e in failures}
    if any(e.type == "orchestrator.semantic.checkpoint.requested" and e.origin == "kernel"
           and e.payload.get("checkpoint") == "candidate_reader_observation"
           and e.payload.get("source_event_id") in ids for e in events):
        return
    current_failures = {c.get("last_event_id") for c in manifest["children"] if c.get("status") == "failed"}
    operations = reduce_workflow_operations(events)
    by_id = {e.id: e for e in events}
    for failure in failures:
        operation_id = failure.payload.get("operation_id")
        op = operations.get(operation_id, {})
        admitted = by_id.get(op.get("call_result_admitted_event_id"))
        if (failure.id not in current_failures or not admitted or admitted.origin != "kernel"
                or admitted.type != "workflow.call.result.admitted" or admitted.payload.get("operation_id") != operation_id
                or admitted.payload.get("control_result_ref") != failure.payload.get("control_result_ref")
                or admitted.payload.get("request_hash") != op.get("request_hash")):
            continue
        try:
            scope, result = candidate_reader_binding(runtime.state_dir, events, fanout_id=fanout_id,
                                                     operation_id=operation_id)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        if (result["verdict"] != "rejected" or not scope["failed_task_ids"] or scope["child_event_id"] != failure.id
                or scope["plan_artifact_package_digest"] != package.get("package_digest")
                or not any(row.get("status") == "failed" for row in result.get("requirement_results", []))):
            continue
        try:
            require_artifact_access(scope["control_result_ref"].get("access_scope"), actor=role.instance_id,
                                    role=role.name, purpose="orchestrator-agent:candidate_reader_observation")
            request_orchestrator_agent_checkpoint(
                runtime, checkpoint="candidate_reader_observation", checkpoint_policy="shadow",
                workflow_run_id=run, source_event=failure, payload={
                    "workflow_run_id": run, "flow_kind": flow, "plan_revision": package.get("plan_revision"),
                    "task_map_generation": scope["task_map_generation"], "target_task_id": scope["anchor_task_id"],
                    "plan_artifact_package_ref": package["package_ref"], "plan_artifact_package_id": package.get("package_id"),
                    "plan_artifact_package_digest": scope["plan_artifact_package_digest"],
                    "candidate_failure_id": failure.id, "candidate_operation_id": result["operation_id"],
                    "artifact_refs": [{**scope["control_result_ref"], "source_id": "candidate-reader-counterexample"}],
                },
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Advisory preparation never delays a sibling or replaces its outcome.
            rejected = any(e.type == "orchestrator.semantic.checkpoint.rejected" and e.origin == "kernel"
                           and e.causation_id == failure.id for e in events)
            if not rejected:
                runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.checkpoint.rejected",
                    origin="kernel", actor="zf-cli", task_id=scope["anchor_task_id"], causation_id=failure.id,
                    correlation_id=run, payload={"checkpoint": "candidate_reader_observation",
                    "source_event_id": failure.id, "checkpoint_policy": "shadow", "reason": str(exc)}))
        return


def admitted_reader_observation(state_dir, events, member_events: set[str]) -> dict:
    """Only carry a previously admitted advisory decision for the same aggregate member."""
    requests = {e.payload.get("operation_id"): e.payload.get("source_event_id") for e in events
                if e.origin == "kernel" and e.type == "orchestrator.semantic.checkpoint.requested"
                and e.payload.get("checkpoint") == "candidate_reader_observation"
                and e.payload.get("checkpoint_policy") == "shadow" and e.payload.get("source_event_id") in member_events}
    for event in reversed(events):
        p = event.payload
        if (event.origin == "kernel" and event.type == "orchestrator.semantic.decision.shadowed"
                and p.get("checkpoint") == "candidate_reader_observation" and p.get("checkpoint_policy") == "shadow"
                and p.get("source_event_id") in member_events and requests.get(p.get("operation_id")) == p["source_event_id"]
                and p.get("decision") == "continue" and p.get("applied") is False and p.get("decision_ref")):
            try:
                hydrate_sidecar_ref(state_dir, p["decision_ref"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            return {**p["decision_ref"], "source_id": "candidate-reader-observation"}
    return {}
