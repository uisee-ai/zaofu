"""Replace only the pinned Critic cancelled by its retired empty successor."""

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.critic_handoff_retry import KIND as HANDOFF, refuse_superseding_work
from zf.runtime.diagnosis_critic_handoff import admitted_unissued_planner
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.fanout_identity import fanout_current_status
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    canonicalize_operation_request,
    operation_request_hash,
    reduce_workflow_operations,
)

KIND = "cancelled_diagnosis_critic_retry"
FIELDS = {
    "retry_cancelled_critic_event_id",
    "prior_critic_dispatch_event_id",
    "prior_critic_request_hash",
    "critic_handoff_event_id",
    "planner_admitted_event_id",
    "candidate_event_id",
    "workflow_run_id",
    "approval_ref",
}


def apply_cancelled_critic_retry(service, requested, action, requested_action, payload):
    with locked_path(service.state_dir / "locks" / "reader-stage-recovery"):
        try:
            if service.actor not in {"operator", "web"}:
                raise ValueError("Cancelled Critic retry requires operator authority")
            marker, replayed = prepare_cancelled_critic_retry(service, requested, payload)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return service._recovery_failed(requested, action, requested_action, str(exc), 409)
        if not replayed:
            marker = service.writer.append(marker)
        return service._recovery_ok(
            requested,
            marker,
            action,
            requested_action,
            {"replayed": replayed, "cancelled_critic_retry_event_id": marker.id},
        )


def _one(rows, message):
    if len(rows) != 1:
        raise ValueError(message)
    return rows[0]


def prepare_cancelled_critic_retry(service, requested, payload):
    if set(payload) != FIELDS or not all(isinstance(v, str) and v.strip() for v in payload.values()):
        raise ValueError(
            "Cancelled Critic retry requires the exact handoff, dispatch, cancellation, freeze and approval"
        )
    state = service.state_dir
    events = service.writer.event_log.read_all()
    by_id = {e.id: e for e in events}

    def kernel(key, kind):
        e = by_id.get(payload[key])
        if not e or (e.type, e.origin, e.actor) != (kind, "kernel", "zf-cli"):
            raise ValueError("Missing canonical " + kind)
        return e

    handoff = kernel("critic_handoff_event_id", "fanout.aggregate.started")
    dispatch = kernel("prior_critic_dispatch_event_id", "fanout.synth.dispatched")
    cancel = kernel("retry_cancelled_critic_event_id", "workflow.operation.cancelled")
    run = payload["workflow_run_id"]
    h = handoff.payload
    d = dispatch.payload
    fanout = h.get("fanout_id")
    existing = next(
        (
            e
            for e in events
            if e.type == "fanout.aggregate.started"
            and e.origin == "kernel"
            and e.payload.get("recovery_kind") == KIND
            and e.payload.get("retry_cancelled_critic_event_id") == cancel.id
        ),
        None,
    )
    if existing:
        if any(existing.payload.get(k) != v for k, v in payload.items()):
            raise ValueError("Cancelled Critic already has a different authorized replacement")
        return existing, True
    wrong = by_id.get(cancel.causation_id)
    if (
        h.get("recovery_kind") != HANDOFF
        or not h.get("retired_empty_fanout_id")
        or not fanout
        or any(
            h.get(k) != payload[k]
            for k in ("workflow_run_id", "planner_admitted_event_id", "candidate_event_id")
        )
        or wrong is None
        or (wrong.type, wrong.actor, wrong.origin) != ("fanout.cancelled", "zf-cli", "kernel")
        or wrong.payload.get("fanout_id") != fanout
        or wrong.payload.get("superseded_by") != h["retired_empty_fanout_id"]
        or wrong.payload.get("source") != "superseded_reader_fanout_manifest_closeout"
        or wrong.payload.get("reason") != "superseded_by_newer_replan_attempt"
        or d.get("fanout_id") != fanout
        or d.get("workflow_run_id") != run
        or not (events.index(handoff) < events.index(dispatch) < events.index(wrong) < events.index(cancel))
        or cancel.payload.get("request_hash") != d.get("request_hash")
        or d.get("request_hash") != payload["prior_critic_request_hash"]
        or cancel.payload.get("workflow_run_id") != run
        or cancel.payload.get("operation_id") != d.get("operation_id")
    ):
        raise ValueError("Critic cancellation was not caused by the retired empty successor")
    proj = build_run_admission_projection(events).runs.get(run)
    manifest = FanoutManifestProjector(state).rebuild(fanout, events)
    freeze = frozen_candidate_for_payload(events, run, payload={})
    if (
        not proj
        or proj.status != "running"
        or not fanout_current_status(events, fanout).current
        or not freeze
        or freeze.id != payload["candidate_event_id"]
        or manifest["aggregate"].get("last_event_id") != wrong.id
        or manifest["aggregate"].get("status") != "cancelled"
        or manifest.get("critic_handoff_retry_event_id") != handoff.id
        or (manifest.get("synth") or {}).get("last_event_id") != dispatch.id
    ):
        raise ValueError("Original cancelled fanout, freeze or Run has been superseded")

    opid = d["operation_id"]
    operation = reduce_workflow_operations(events, workflow_run_id=run).get(opid, {})
    if (
        operation.get("status") != "cancelled"
        or operation.get("last_event_id") != cancel.id
        or operation.get("call_result_admitted_event_id")
        or operation.get("request_hash") != d["request_hash"]
        or operation.get("active_attempt_id") != d.get("attempt_id")
    ):
        raise ValueError("Original Critic operation is no longer an unconsumed cancellation")
    until_first = events[: events.index(handoff) + 1]
    producer = admitted_unissued_planner(
        state, until_first, fanout_id=fanout, trigger_event_id=manifest["trigger_event_id"]
    )
    if not producer or any(h.get(k) != v for k, v in producer.items()):
        raise ValueError("Original approved handoff lost its signed Planner binding")
    envelope = hydrate_sidecar_ref(state, operation["request_ref"]).payload
    request = envelope["request"]
    scope = request.get("result_identity", {}).get("diagnosis_review", {})
    if (
        operation_request_hash(envelope) != d["request_hash"]
        or request.get("output_profile_id") != "plan-synth"
        or scope.get("planner_admitted_event_id") != payload["planner_admitted_event_id"]
        or scope.get("candidate_event_id") != freeze.id
        or scope.get("source_commit") != freeze.payload.get("candidate_head_commit")
        or not scope.get("finding_ids")
        or not request.get("required_reads")
    ):
        raise ValueError("Cancelled signed request no longer binds its frozen Planner diagnosis")
    manifest_ref = d["attempt_source_manifest"]
    if manifest_ref["sha256"] != request["source_manifest_digest"]:
        raise ValueError("Original Critic source manifest differs from its signed request")
    original_sources = hydrate_sidecar_ref(state, manifest_ref).payload
    if not any(
        r.get("source_id") == "child-planner-control-result"
        and r.get("sha256") == producer["planner_result_ref"]["sha256"]
        for r in original_sources["sources"]
    ):
        raise ValueError("Original Critic manifest omitted its admitted Planner control result")
    after = events[len(until_first) :]
    issued = _one(
        [
            e
            for e in after
            if e.type == "workflow.operation.requested"
            and e.origin == "kernel"
            and e.payload.get("operation_id") == opid
        ],
        "Original Critic must have one signed issuance",
    )
    _one(
        [
            e
            for e in after
            if e.type == "worker.state.changed"
            and e.origin == "kernel"
            and e.actor == d.get("role_instance")
            and e.payload.get("fanout_cancelled_event_id") == wrong.id
            and e.payload.get("operation_id") == opid
            and e.payload.get("request_hash") == d["request_hash"]
            and e.payload.get("provider_stop_confirmed") is True
        ],
        "Cancelled Critic requires confirmed provider stop",
    )
    _one(
        [
            e
            for e in after
            if e.type == "task.attempt.superseded"
            and e.origin == "kernel"
            and e.causation_id == cancel.id
            and e.payload.get("operation_id") == opid
            and e.payload.get("dispatch_id") == d.get("attempt_id")
        ],
        "Original provider attempt is still outstanding",
    )
    if any(
        e.type == "fanout.started"
        and e.payload.get("workflow_run_id") == run
        and e.payload.get("stage_id") == manifest["stage_id"]
        for e in after
    ):
        raise ValueError("A newer Plan fanout replaced the cancelled Critic")
    roles = {d["role_instance"], *(c["role_instance"] for c in manifest["children"])}
    refuse_superseding_work(
        state,
        events,
        [e for e in after if e.id != issued.id],
        run,
        roles,
        {fanout, h["retired_empty_fanout_id"]},
    )
    return ZfEvent(
        type="fanout.aggregate.started",
        actor="zf-cli",
        origin="kernel",
        task_id=handoff.task_id,
        correlation_id=run,
        causation_id=requested.id,
        payload={
            **payload,
            "fanout_id": fanout,
            "stage_id": manifest["stage_id"],
            "trace_id": run,
            "mode": manifest["aggregate_config"]["mode"],
            "recovery_kind": KIND,
            "fanout_cancelled_event_id": wrong.id,
            "prior_critic_checkpoint": {
                "request": operation["request_ref"],
                "manifest": manifest_ref,
                "request_hash": d["request_hash"],
            },
        },
    ), False


def verify_replacement_inputs(state, checkpoint, replacement, manifest):
    """Allow new transport identifiers; compare every declared source and signed read before issuance."""
    replacement = canonicalize_operation_request(replacement)
    old_envelope = hydrate_sidecar_ref(state, checkpoint["request"]).payload
    old = old_envelope["request"]
    old_manifest = hydrate_sidecar_ref(state, checkpoint["manifest"]).payload
    if (
        operation_request_hash(old_envelope) != checkpoint["request_hash"]
        or old["source_manifest_digest"] != checkpoint["manifest"]["sha256"]
    ):
        raise ValueError("Cancelled Critic checkpoint does not match the original signature")
    fields = (
        "result_identity",
        "required_reads",
        "output_profile_id",
        "output_profile_revision",
        "role_instance",
        "stage_id",
        "fanout_id",
        "workflow_run_id",
        "task_id",
        "workdir_write_scopes",
    )
    changed = [k for k in fields if replacement.get(k) != old.get(k)]
    if changed:
        fields_in_identity = [
            key
            for key in old["result_identity"]
            if old["result_identity"].get(key) != replacement["result_identity"].get(key)
        ]
        changed_reads = [
            r["source_id"] for r in old["required_reads"] if r not in replacement["required_reads"]
        ]
        raise ValueError(
            f"Replacement Critic changed {changed}; identity fields {fields_in_identity}; read sources {changed_reads}"
        )

    def source_bindings(sources):
        fields = ("source_id", "artifact_id", "sha256", "allowed_paths", "access_scope", "read_selections")
        return [{key: s.get(key) for key in fields} for s in sources["sources"]]

    if source_bindings(manifest) != source_bindings(old_manifest):
        raise ValueError("Replacement Critic changed the original source roster, content or access scope")
