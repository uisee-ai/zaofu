"""Requeue a displaced, already owner-authorized Plan checkpoint on a paused Run."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.fanout_identity import fanout_current_statuses
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_scope import run_aliases
from zf.runtime.stage_replan_generation import completed_generation_source, escalation_generation_id
from zf.runtime.writer_replan_frontier import retired_plan_branch


KEY = "redrive_owner_trigger_event_id"
FIELDS = {KEY, "workflow_run_id", "escalation_event_id", "approval_ref"}
ACTION = "stage-replan-new-generation"


def _web_request(event, action):
    return (
        event is not None
        and event.type == "web.action.requested"
        and event.actor == "web"
        and event.origin in {"", "kernel"}
        and event.payload.get("action") == action
        and not event.payload.get("source_session_id")
    )


def _checkpoint(events, service, requested, payload):
    run = payload["workflow_run_id"]
    by_id = {e.id: e for e in events}
    if (
        service.actor != "web"
        or service.surface != "web"
        or not _web_request(requested, ACTION)
        or by_id.get(requested.id) != requested
        or requested.payload.get("request") != payload
    ):
        raise ValueError("a token-gated owner Web request is required")
    admission = build_run_admission_projection(events).runs.get(run)
    if not admission or admission.status != "paused":
        raise ValueError("the exact workflow Run must be paused")
    pause = next(
        (e for e in reversed(events) if e.type == "run.paused" and e.payload.get("workflow_run_id") == run),
        None,
    )
    pause_request = by_id.get(pause.causation_id) if pause else None
    if (
        not pause_request
        or pause.actor not in {"zf-cli", "web", "operator"}
        or pause.origin not in {"", "kernel"}
        or pause_request.id != pause.payload.get("source_event_id")
        or pause_request.actor != pause.actor
        or pause_request.origin not in {"", "kernel"}
        or pause_request.type not in {"web.action.requested", "control.action.requested"}
        or pause_request.payload.get("action") != "run-pause"
    ):
        raise ValueError("missing the admitted owner pause request for this Run")
    trigger = by_id.get(payload[KEY])
    if (
        not trigger
        or trigger.actor != "zf-cli"
        or trigger.origin not in {"", "kernel"}
        or trigger.payload.get("workflow_run_id") != run
    ):
        raise ValueError("owner trigger does not belong to this Run")
    scope = trigger.payload
    if (
        scope.get("stage_replan_generation")
        != escalation_generation_id(run, payload["escalation_event_id"])
        or scope.get("stage_replan_generation_source_event_id") != payload["escalation_event_id"]
        or scope.get("operator_authorization_ref") != payload["approval_ref"]
    ):
        raise ValueError("original escalation or owner approval does not match")
    owner = by_id.get(trigger.causation_id)
    if not _web_request(owner, ACTION):
        raise ValueError("missing original owner Web authorization")
    author = owner.payload.get("request") or {}
    if any(
        author.get(k) != payload[k] for k in ("workflow_run_id", "escalation_event_id", "approval_ref")
    ) or author.get("operator_feedback", []) != scope.get("operator_feedback", []):
        raise ValueError("owner request does not bind this checkpoint and feedback")
    stage = next(
        (
            s
            for s in service.config.workflow.stages
            if s.id == scope.get("stage_id")
            and s.trigger == trigger.type
            and s.topology == "fanout_reader"
            and s.attempt_domain == "plan"
        ),
        None,
    )
    if not stage:
        raise ValueError("checkpoint is not a configured Plan Reader trigger")
    # Also fence an incomplete newer publication whose goal is visible before its receipt.
    goal = next(
        (
            e
            for e in reversed(events)
            if e.type == "run.goal.updated"
            and e.origin in {"", "kernel"}
            and e.payload.get("workflow_run_id") == run
            and e.payload.get("stage_replan_generation")
        ),
        None,
    )
    aliases = run_aliases(events)
    receipt = next(
        (
            e
            for e in reversed(events)
            if e.type == "runtime.action.completed"
            and e.actor == "web"
            and e.origin in {"", "kernel"}
            and e.causation_id == trigger.id
            and e.payload.get("stage_trigger_event_id") == trigger.id
            and completed_generation_source(e, by_id, aliases)
        ),
        None,
    )
    if (
        not goal
        or not receipt
        or receipt.payload.get("run_goal_update_event_id") != goal.id
        or goal.causation_id != owner.id
    ):
        raise ValueError("checkpoint lacks the current completed owner generation")
    existing = next(
        (e for e in events if e.type == trigger.type and e.payload.get("redrive_of") == trigger.id), None
    )
    if existing:
        earlier = by_id.get(existing.causation_id)
        if (
            existing.actor != "web"
            or existing.origin != "kernel"
            or not _web_request(earlier, ACTION)
            or any((earlier.payload.get("request") or {}).get(k) != payload[k] for k in FIELDS)
            or existing.payload != {**scope, "redrive_of": trigger.id, "rework_of": trigger.id}
        ):
            raise ValueError("checkpoint already has a different redrive")
        return existing, True
    fanouts = {
        e.payload["fanout_id"]: e
        for e in events
        if e.type == "fanout.started"
        and e.actor == "zf-cli"
        and e.origin == "kernel"
        and e.payload.get("workflow_run_id") == run
        and e.payload.get("stage_id") == stage.id
    }
    originals = [fid for fid, e in fanouts.items() if e.payload.get("trigger_event_id") == trigger.id]
    statuses = fanout_current_statuses(events)
    if len(originals) != 1 or originals[0] not in statuses:
        raise ValueError("no unique dispatched owner fanout")
    original = originals[0]
    successor = fanouts.get(statuses[original].superseded_by)
    if not successor or not statuses[successor.payload["fanout_id"]].current:
        raise ValueError("owner Plan has no current displacing fanout")
    other = by_id.get(successor.payload.get("trigger_event_id"))
    if (
        not other
        or other.payload.get("workflow_run_id") != run
        or other.payload.get("stage_replan_generation") == scope.get("stage_replan_generation")
        or not retired_plan_branch(events, other, service.config)
    ):
        raise ValueError("the current successor is not a proven retired Plan branch")
    operations = {
        e.payload["operation_id"]
        for e in events
        if e.type == "fanout.child.dispatched"
        and e.origin == "kernel"
        and e.payload.get("fanout_id") == original
        and e.payload.get("operation_id")
    }
    cancelled = {
        e.payload.get("operation_id")
        for e in events
        if e.type == "workflow.operation.cancelled"
        and e.origin == "kernel"
        and e.payload.get("workflow_run_id") == run
        and str(e.payload.get("reason", "")).startswith("superseded_reader_fanout:")
    }
    if not operations or not operations <= cancelled:
        raise ValueError("owner operations have not all been cancelled by supersession")
    if any(
        e.type in {"fanout.child.completed", "fanout.synth.completed", "fanout.aggregate.completed"}
        and e.payload.get("fanout_id") == original
        for e in events
    ):
        raise ValueError("the authorized Reader already delivered a result")
    downstream = {"task_map.ready", "plan.approval.requested", "plan.approved", "candidate.ready"}
    if any(
        e.type in downstream
        and e.origin in {"", "kernel"}
        and run in {e.payload.get("workflow_run_id"), e.payload.get("run_id"), e.correlation_id}
        for e in events[events.index(trigger) + 1 :]
    ):
        raise ValueError("a later business checkpoint already owns this Run")
    return ZfEvent(
        type=trigger.type,
        actor="web",
        origin="kernel",
        task_id=trigger.task_id,
        correlation_id=trigger.correlation_id,
        causation_id=requested.id,
        payload={**scope, "redrive_of": trigger.id, "rework_of": trigger.id},
    ), False


def apply_owner_redrive(service, requested, action, requested_action, payload):
    try:
        if not FIELDS <= set(payload) <= FIELDS | {"reason"} or any(
            not isinstance(v, str) or not v.strip() for v in payload.values()
        ):
            raise ValueError(
                "owner Plan redrive requires exactly the Run, original trigger, escalation and approval"
            )
        marker, replayed = _checkpoint(service.writer.event_log.read_all(), service, requested, payload)
    except ValueError as exc:
        return service._recovery_failed(requested, action, requested_action, str(exc), 409)
    if not replayed:
        marker = service.writer.append(marker)
    return service._recovery_ok(
        requested,
        marker,
        action,
        requested_action,
        {
            "replayed": replayed,
            "retriggered_event_id": marker.id,
            "source_event_id": payload[KEY],
            "run_id": payload["workflow_run_id"],
            "stage_replan_generation": marker.payload["stage_replan_generation"],
        },
    )


def apply_stage_replan(service, requested, action, requested_action, payload):
    # The same lock serializes publication with Run pause, resume and competing owner generations.
    with locked_path(service.state_dir / "locks" / "run-admission"):
        from zf.runtime.plan_derived_claim_correction import KEY as CORRECTION, apply_owner_correction

        if CORRECTION in payload:
            return apply_owner_correction(service, requested, action, requested_action, payload)
        if KEY in payload:
            return apply_owner_redrive(service, requested, action, requested_action, payload)
        return service._stage_replan_new_generation_action(
            requested=requested, action=action, requested_action=requested_action, payload=payload
        )
