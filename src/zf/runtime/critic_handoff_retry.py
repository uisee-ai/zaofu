"""Reopen an unissued diagnosis Critic against its admitted Planner output."""

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.fanout_identity import fanout_current_status
from zf.runtime.plan_candidate_owner_review import bound_candidate_diagnosis
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash

KIND = "diagnosis_critic_handoff_retry"
FIELDS = {
    "retry_critic_handoff_event_id",
    "planner_admitted_event_id",
    "planner_control_sha256",
    "workflow_run_id",
    "candidate_event_id",
    "approval_ref",
}


def apply_critic_recovery(service, requested, action, requested_action, payload):
    if "retry_cancelled_critic_event_id" in payload:
        from zf.runtime.cancelled_critic_recovery import apply_cancelled_critic_retry

        return apply_cancelled_critic_retry(service, requested, action, requested_action, payload)
    with locked_path(service.state_dir / "locks" / "reader-stage-recovery"):
        try:
            if service.actor not in {"operator", "web"}:
                raise ValueError("Critic handoff repair requires operator authority")
            marker, replayed = prepare_handoff_retry(service, requested, payload)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return service._recovery_failed(requested, action, requested_action, str(exc), 409)
        if not replayed:
            marker = service.writer.append(marker)
        return service._recovery_ok(
            requested,
            marker,
            action,
            requested_action,
            {"replayed": replayed, "critic_handoff_event_id": marker.id},
        )


def prepare_handoff_retry(service, requested, payload):
    from zf.runtime.diagnosis_critic_handoff import admitted_unissued_planner

    if set(payload) != FIELDS or not all(isinstance(v, str) and v.strip() for v in payload.values()):
        raise ValueError(
            "Critic handoff recovery requires an exact failure, Planner, freeze and approval binding"
        )
    events = service.writer.event_log.read_all()
    by_id = {e.id: e for e in events}
    failure = by_id.get(payload["retry_critic_handoff_event_id"])
    if (
        failure is None
        or failure.type != "fanout.synth.completed"
        or failure.origin != "kernel"
        or failure.actor != "zf-cli"
        or failure.payload.get("status") != "failed"
        or any(
            failure.payload.get(k) for k in ("report", "operation_id", "request_hash", "control_result_ref")
        )
    ):
        raise ValueError("Only a kernel pre-dispatch failure without a Critic verdict may retry")
    fanout = failure.payload["fanout_id"]
    prior = next(
        (
            e
            for e in events
            if e.type == "fanout.aggregate.started"
            and e.origin == "kernel"
            and e.payload.get("recovery_kind") == KIND
            and e.payload.get("retry_critic_handoff_event_id") == failure.id
        ),
        None,
    )
    if prior:
        if any(prior.payload.get(k) != v for k, v in payload.items()):
            raise ValueError("Critic handoff already recovered with another binding")
        return prior, True

    if any(
        e.type == "fanout.aggregate.started"
        and e.origin == "kernel"
        and e.payload.get("fanout_id") == fanout
        and e.payload.get("recovery_kind") == KIND
        and e.payload.get("approval_ref") == payload["approval_ref"]
        for e in events
    ):
        raise ValueError("A different handoff failure cannot reuse the prior recovery approval")
    manifest = FanoutManifestProjector(service.state_dir).rebuild(fanout, events)
    trigger = by_id.get(manifest.get("trigger_event_id"))
    run = payload["workflow_run_id"]
    scope = trigger.payload if trigger else {}
    frozen = frozen_candidate_for_payload(events, run, payload={})
    if (
        not trigger
        or trigger.actor != "zf-cli"
        or not bound_candidate_diagnosis(scope)
        or manifest.get("workflow_run_id") != run
        or failure.correlation_id != run
        or frozen is None
        or frozen.id != payload["candidate_event_id"]
        or frozen.id != scope.get("candidate_event_id")
        or frozen.payload.get("candidate_head_commit") != scope.get("source_commit")
        or frozen.payload.get("task_map_generation") != scope.get("task_map_generation")
        or frozen.payload.get("plan_artifact_package_digest") != scope.get("plan_artifact_package_digest")
    ):
        raise ValueError("Critic failure no longer binds the authorized frozen OA diagnosis")
    entry = build_run_admission_projection(events).runs.get(run)
    if entry is None or entry.status != "running":
        raise ValueError("Critic handoff requires an active admitted Run")
    if (
        (manifest.get("synth") or {}).get("last_event_id") != failure.id
        or manifest["aggregate"].get("synth_event_id") != failure.id
        or manifest["aggregate"].get("status") != "failed"
        or failure.payload.get("role_instance") != manifest["aggregate_config"].get("synth_role")
    ):
        raise ValueError("Failed Critic handoff is no longer the original fanout outcome")
    upstream = admitted_unissued_planner(
        service.state_dir, events, fanout_id=fanout, trigger_event_id=trigger.id
    )
    if (
        not upstream
        or upstream["planner_admitted_event_id"] != payload["planner_admitted_event_id"]
        or upstream["planner_result_ref"]["sha256"] != payload["planner_control_sha256"]
    ):
        raise ValueError("Critic handoff does not match the admitted, unreviewed Planner result")

    stage = manifest["stage_id"]
    later = events[events.index(failure) + 1 :]
    roles = {failure.payload["role_instance"], *(c["role_instance"] for c in manifest["children"])}
    carriers = {fanout} | {
        e.payload.get("fanout_id")
        for e in later
        if e.type == "fanout.started"
        and e.payload.get("stage_id") == stage
        and e.payload.get("workflow_run_id") == run
    }
    refuse_superseding_work(service.state_dir, events, later, run, roles, carriers)
    displaced = _empty_descendant(events, later, failure, manifest, trigger, by_id)
    current = fanout_current_status(events, fanout)
    if not current.known or (not current.current and current.superseded_by != displaced):
        raise ValueError("Another fanout owns this diagnosis stage")
    return ZfEvent(
        type="fanout.aggregate.started",
        actor="zf-cli",
        origin="kernel",
        task_id=manifest["pdd_id"],
        correlation_id=run,
        causation_id=requested.id,
        payload={
            **payload,
            **upstream,
            "fanout_id": fanout,
            "stage_id": stage,
            "trace_id": run,
            "mode": manifest["aggregate_config"]["mode"],
            "recovery_kind": KIND,
            "retired_empty_fanout_id": displaced,
        },
    ), False


def refuse_superseding_work(state_dir, events, later, run, roles, carriers):
    requests = {
        e.payload.get("operation_id"): e for e in events if e.type == "workflow.operation.requested"
    }
    for e in later:
        p = e.payload
        if (p.get("workflow_run_id") or p.get("trace_id") or e.correlation_id) != run:
            continue
        if e.type in {"workflow.operation.requested", "workflow.call.result.admitted"}:
            issue = e if e.type == "workflow.operation.requested" else requests.get(p.get("operation_id"))
            if issue is None or not issue.payload.get("request_ref"):
                raise ValueError("A later operation has no attributable signed request")
            envelope = hydrate_sidecar_ref(state_dir, issue.payload["request_ref"]).payload
            signed = envelope["request"]
            if (
                operation_request_hash(envelope) != issue.payload["request_hash"]
                or signed.get("fanout_id") in carriers
                or signed.get("role_instance") in roles
                or p.get("role_instance") in roles
            ):
                raise ValueError("A later operation supersedes the unissued Critic handoff")
        if e.type in {
            "task_map.ready",
            "task_map.admitted",
            "plan.approved",
            "candidate.ready",
            "run.goal.completed",
            "run.failed",
            "run.cancelled",
        }:
            raise ValueError("A later authority supersedes the unissued Critic handoff")


def _empty_descendant(events, later, failure, manifest, trigger, by_id):
    run, stage = manifest["workflow_run_id"], manifest["stage_id"]
    starts = [
        e
        for e in later
        if e.type == "fanout.started"
        and e.payload.get("workflow_run_id") == run
        and e.payload.get("stage_id") == stage
    ]
    if not starts:
        return ""
    if len(starts) != 1:
        raise ValueError("Only one causally empty successor can be retired")
    start = starts[0]
    successor = start.payload["fanout_id"]
    retry = by_id.get(start.payload.get("trigger_event_id"))
    previous = by_id.get(retry.causation_id) if retry else None
    scopes = (
        "workflow_run_id",
        "source_commit",
        "candidate_event_id",
        "task_map_generation",
        "plan_artifact_package_digest",
        "orchestrator_decision_ref",
        "operator_feedback",
    )
    descendant_terminal = [
        e
        for e in later
        if e.type == "fanout.aggregate.completed"
        and e.origin == "kernel"
        and e.payload.get("fanout_id") == successor
    ]
    if (
        not retry
        or not previous
        or start.origin != "kernel"
        or start.causation_id != retry.id
        or retry.origin != "kernel"
        or retry.type != trigger.type
        or retry.payload.get("rework_of") != previous.id
        or previous.type != manifest["aggregate_config"]["failure_event"]
        or previous.origin != "kernel"
        or previous.causation_id != failure.id
        or previous.payload.get("fanout_id") != manifest["fanout_id"]
        or any(retry.payload.get(k) != trigger.payload.get(k) for k in scopes)
        or not bound_candidate_diagnosis(retry.payload)
        or len(descendant_terminal) != 1
        or descendant_terminal[0].payload.get("status") != "failed"
        or descendant_terminal[0].payload.get("failure_kind") != "infra"
        or any(
            e.payload.get("fanout_id") == successor
            and e.type in {"fanout.child.dispatched", "fanout.child.completed", "fanout.synth.dispatched"}
            for e in events
        )
    ):
        raise ValueError("Successor is not an empty, failed technical retry of this exact diagnosis")
    return successor
