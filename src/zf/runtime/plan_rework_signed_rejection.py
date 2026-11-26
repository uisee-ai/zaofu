"""Prove which submitted Plan an independent, admitted Critic rejected."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.plan_synth_recovery_refs import resume_failed_critic_candidate
from zf.runtime.run_scope import run_aliases
from zf.runtime.stage_replan_generation import (
    completed_generation_source,
    escalation_generation_id,
)


def _from_kernel(event, kind, run):
    return (
        event
        and event.type == kind
        and event.origin == "kernel"
        and event.actor == "zf-cli"
        and event.payload.get("workflow_run_id") == run
    )


def _one(matches, description):
    values = list(matches)
    if len(values) != 1:
        raise ValueError(f"expected one {description}")
    return values[0]


def _owner_checkpoint(events, by_id, *, trigger, failure, synth, run):
    """The stock Web owner action explicitly selected this exact rejected checkpoint."""
    requested = by_id.get(trigger.causation_id)
    p = trigger.payload
    if (
        not requested
        or requested.type != "web.action.requested"
        or requested.actor != "web"
        or requested.origin not in {"", "kernel"}
        or requested.payload.get("source_session_id")
        or requested.payload.get("action") != "stage-replan-new-generation"
    ):
        raise ValueError("no owner request for this rejected Critic checkpoint")
    body = requested.payload.get("request") or {}
    authorization = p.get("operator_authorization_ref")
    escalation_id = p.get("stage_replan_generation_source_event_id")
    if (
        not authorization
        or not escalation_id
        or p.get("operator_authorized") is not True
        or p.get("resume_candidate_synth_event_id") != synth.id
        or body.get("resume_candidate_synth_event_id") != synth.id
        or body.get("approval_ref") != authorization
        or body.get("escalation_event_id") != escalation_id
        or body.get("workflow_run_id") != run
        or p.get("stage_replan_generation")
        != escalation_generation_id(run, escalation_id)
    ):
        raise ValueError("owner grant does not select this Run, escalation and Critic")
    receipt = _one(
        (
            e
            for e in events
            if e.type == "runtime.action.completed"
            and e.actor == requested.actor
            and e.origin in {"", "kernel"}
            and e.causation_id == trigger.id
            and e.payload.get("source_failure_event_id") == failure.id
            and e.payload.get("stage_trigger_event_id") == trigger.id
            and not e.payload.get("replayed")
        ),
        "applied owner generation receipt",
    )
    prior = {e.id: e for e in events[: events.index(receipt)]}
    escalation = completed_generation_source(receipt, prior, run_aliases(events))
    if (
        not escalation
        or escalation.id != escalation_id
        or escalation.payload.get("source_event_id") != failure.id
        or "start_new_generation"
        not in (escalation.payload.get("allowed_actions") or [])
        or not events.index(failure)
        < events.index(escalation)
        < events.index(requested)
        < events.index(trigger)
    ):
        raise ValueError("owner generation is not bound to the failed review")
    return {
        "generation_approval_event_id": receipt.id,
        "generation_request_event_id": requested.id,
    }


def signed_rejection_source(
    state: Path,
    events: Sequence[ZfEvent],
    *,
    scope: Mapping,
    start: ZfEvent,
    trigger: ZfEvent,
    request: Mapping,
) -> tuple[ZfEvent, ZfEvent, ZfEvent, list[dict], dict]:
    """Bind the recipient to the rejected review. Physical file and ACL checks belong to the caller."""
    by_id = {e.id: e for e in events}
    run, stage = request["workflow_run_id"], request["stage_id"]
    if (
        not _from_kernel(start, "fanout.started", run)
        or start.payload.get("stage_id") != stage
        or start.payload.get("fanout_id") != request.get("fanout_id")
        or not trigger
        or start.causation_id != trigger.id
        or start.payload.get("trigger_event_id") != trigger.id
        or trigger.payload.get("workflow_run_id") != run
        or scope.get("rework_of") != trigger.payload.get("rework_of")
    ):
        raise ValueError("rejected review has no bound recipient fanout")
    failure = by_id.get(scope.get("rework_of"))
    failure_kind = (start.payload.get("aggregate") or {}).get("failure_event")
    if (
        not failure_kind
        or not _from_kernel(failure, failure_kind, run)
        or failure.payload.get("stage_id") != stage
        or failure.payload.get("status") != "failed"
    ):
        raise ValueError("missing current stage failure for the rejected review")
    synth = by_id.get(failure.causation_id)
    fid = failure.payload.get("fanout_id")
    if (
        not synth
        or synth.type != "fanout.synth.completed"
        or not fid
        or synth.payload.get("fanout_id") != fid
        or not events.index(synth)
        < events.index(failure)
        < events.index(trigger)
        < events.index(start)
    ):
        raise ValueError("missing causal Critic result")
    old_start = _one(
        (
            e
            for e in events[: events.index(synth)]
            if _from_kernel(e, "fanout.started", run)
            and e.payload.get("fanout_id") == fid
            and e.payload.get("stage_id") == stage
        ),
        "producer fanout",
    )
    old = by_id.get(old_start.causation_id)
    reviewer = (old_start.payload.get("aggregate") or {}).get("synth_role")
    if (
        not old
        or old_start.payload.get("trigger_event_id") != old.id
        or old.payload.get("workflow_run_id") != run
        or not reviewer
        or reviewer == request.get("role_instance")
        or reviewer != synth.actor
        or synth.payload.get("role_instance") != reviewer
    ):
        raise ValueError(
            "independent Critic does not match the reviewed producer fanout"
        )

    admission = _one(
        (
            e
            for e in events[: events.index(failure)]
            if _from_kernel(e, "workflow.call.result.admitted", run)
            and e.payload.get("source_event_id") == synth.id
        ),
        "kernel Critic admission",
    )
    key_fields = ("operation_id", "attempt_id", "request_hash", "control_result_ref")
    if (
        admission.payload.get("admission_status") != "admitted"
        or admission.payload.get("semantic_verdict") != "rejected"
        or any(
            not synth.payload.get(k) or admission.payload.get(k) != synth.payload[k]
            for k in key_fields
        )
    ):
        raise ValueError("Critic admission differs from the rejected result")
    dispatches = [
        e
        for e in events[: events.index(admission)]
        if e.type == "fanout.synth.dispatched"
        and e.payload.get("operation_id") == synth.payload["operation_id"]
        and e.payload.get("attempt_id") == synth.payload["attempt_id"]
    ]
    if not dispatches or any(
        not _from_kernel(e, "fanout.synth.dispatched", run)
        or e.payload.get("stage_id") != stage
        or e.payload.get("fanout_id") != fid
        or e.payload.get("role_instance") != reviewer
        or e.payload.get("request_hash") != synth.payload["request_hash"]
        or events.index(e) <= events.index(old_start)
        for e in dispatches
    ):
        raise ValueError("Critic dispatch differs from the admitted review")

    if any(
        e.payload.get("workflow_run_id") == run
        and (
            e.type in {"task_map.ready", "plan.approved"}
            or (e.type == "fanout.started" and e.payload.get("stage_id") == stage)
        )
        for e in events[events.index(failure) + 1 : events.index(start)]
    ):
        raise ValueError("a later Plan or author already superseded this rejection")
    provenance = {
        "critic_admission_event_id": admission.id,
        "critic_control_sha256": admission.payload["control_result_ref"]["sha256"],
    }
    generation = trigger.payload.get("stage_replan_generation")
    if (
        scope.get("stage_replan_generation") != generation
        or any(
            scope.get(k) != trigger.payload.get(k)
            or old.payload.get(k) != failure.payload.get(k)
            for k in ("task_map_generation", "plan_artifact_package_digest")
        )
        or any(
            old.payload.get(k) != scope.get(k)
            for k in ("task_map_generation", "plan_artifact_package_digest")
        )
    ):
        raise ValueError("rejected candidate or formal comparison baseline changed")
    if old.payload.get("stage_replan_generation") != failure.payload.get(
        "stage_replan_generation"
    ):
        raise ValueError("producer generation differs from the failed review")
    if failure.payload.get("stage_replan_generation") == generation:
        if trigger.causation_id != failure.id or not _from_kernel(
            trigger, trigger.type, run
        ):
            raise ValueError("a same-generation retry must be caused by this failure")
    else:
        provenance.update(
            _owner_checkpoint(
                events, by_id, trigger=trigger, failure=failure, synth=synth, run=run
            )
        )
    refs = resume_failed_critic_candidate(
        state, events[: events.index(failure) + 1], failure, synth.id
    )
    if not refs or any(
        refs != p.get("previous_plan_candidate_refs")
        for p in (scope, trigger.payload, failure.payload)
    ):
        raise ValueError("recipient refs differ from the Critic's signed review target")
    return old_start, synth, failure, refs, provenance
