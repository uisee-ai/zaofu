"""Bridge durable worker questions to the existing single OA advisory scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.artifact_read_capability import authenticate_role_from_environment
from zf.runtime.durable_mailbox_index import MailboxIndex
from zf.runtime.pause_lifecycle import (
    active_request_age_seconds,
    is_run_dispatch_paused,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

ACTOR = "durable-mailbox"
NOTICE = "workflow.mailbox.notification"
ACCEPTED = "workflow.mailbox.accepted"
QUESTION = "workflow.mailbox.question"
REQUEST = "worker.assistance.requested"
CLOSED = "worker.assistance.closed"


def runtime_view(state_dir, writer, config):
    return SimpleNamespace(
        state_dir=state_dir,
        config=config,
        event_log=writer.event_log,
        event_writer=writer,
    )


def _mail_fact(event, kind):
    return event.type == kind and event.actor == ACTOR and event.origin == "kernel"


def accepted_question(events, request_id):
    return next(
        (
            e
            for e in events
            if _mail_fact(e, ACCEPTED) and e.payload.get("question_id") == request_id
        ),
        None,
    )


def ensure_question(runtime, request, events):
    """A crash after intake and before mail admission re-enters this exact post."""
    if request.origin != "kernel" or not request.payload.get("durable_mailbox"):
        raise ValueError("durable intake requires an authenticated worker request")
    if existing := accepted_question(events, request.payload["request_id"]):
        return existing.payload
    from zf.runtime.durable_mailbox import post

    p = request.payload
    body = hydrate_sidecar_ref(runtime.state_dir, p["recovery_context_ref"]).payload
    # References in the question are locators supplied by its author. No attachment authority is granted.
    text = json.dumps(
        {k: body[k] for k in ("question", "tried", "evidence_refs")}, ensure_ascii=False
    )
    identity = {
        "actor": request.actor,
        "workflow_run_id": p["workflow_run_id"],
        "operation_id": p["operation_id"],
        "attempt_id": p["active_attempt_id"],
        "lease_id": p["lease_id"],
    }
    return post(
        runtime,
        identity,
        request_id=p["request_id"],
        message_id=p["request_id"],
        recipients=[{"mode": "coordination", "owner": "orchestrator"}],
        kind="question",
        text=text,
        question_id=p["request_id"],
        visibility="operator_metadata",
    )


def question_deadline_expired(events, question_id, now=None):
    request = next(
        (
            e
            for e in events
            if e.type == REQUEST
            and e.origin == "kernel"
            and e.payload.get("request_id") == question_id
        ),
        None,
    )
    if not request:
        return False
    timeout = request.payload.get("timeout_seconds") or 0
    return (
        timeout > 0
        and active_request_age_seconds(
            events,
            created_at=datetime.fromisoformat(request.ts),
            now=now or datetime.now(timezone.utc),
            run_id=request.payload["workflow_run_id"],
        )
        >= timeout
    )


def advisory_identity(runtime, request_id):
    """A role credential grants no Run until an actual assistance dispatch names that owner."""
    credential = authenticate_role_from_environment(runtime.state_dir)
    events = MailboxIndex(runtime).read(
        {"actor": credential["role_instance"], "assistance_request_id": request_id},
        select_versions=lambda *_: [],
    )
    request = next(
        (
            e
            for e in events
            if e.type == REQUEST
            and e.origin == "kernel"
            and e.payload.get("request_id") == request_id
            and e.payload.get("durable_mailbox")
        ),
        None,
    )
    if not request or request.payload["owner_instance"] != credential["role_instance"]:
        raise ValueError("no advisory delivery for this recipient")
    accepted = accepted_question(events, request_id)
    if not accepted or not any(
        _mail_fact(e, NOTICE)
        and e.payload.get("request_id") == request_id
        and e.payload.get("message_key") == accepted.payload["message_key"]
        and e.payload.get("owner_instance") == credential["role_instance"]
        and e.payload.get("status") in {"attempted", "confirmed", "unknown"}
        for e in events
    ):
        raise ValueError("advisory dispatch has not reached this owner")
    from zf.runtime.provider_turn_liveness import active_codex_turn_from_events

    turn = active_codex_turn_from_events(events, credential["role_instance"]) or {}
    return {
        "provider_session_id": turn.get("session_id", ""),
        "provider_turn_id": turn.get("turn_id", ""),
        "actor": credential["role_instance"],
        "credential_generation": credential["generation"],
        "role": credential["role_name"],
        "provider": credential["provider"],
        "assistance_request_id": request_id,
        "workflow_run_id": request.payload["workflow_run_id"],
    }


def question_answer(runtime, identity, message_key):
    from zf.runtime.durable_mailbox import read_answer

    answer = read_answer(runtime, identity, message_id=message_key)
    if answer["status"] == "answered":
        return answer
    events = MailboxIndex(runtime).read(identity, select_versions=lambda *_: [])
    accepted = next(
        (
            e
            for e in events
            if _mail_fact(e, ACCEPTED) and e.payload["message_key"] == message_key
        ),
        None,
    )
    rid = accepted.payload.get("question_id") if accepted else None
    closed = next(
        (
            e
            for e in reversed(events)
            if e.type == CLOSED
            and e.origin == "kernel"
            and rid
            and e.payload.get("request_id") == rid
            and e.payload.get("status") in {"waiting", "stale"}
        ),
        None,
    )
    if closed:
        return {
            **answer,
            "status": closed.payload["status"],
            "reason": closed.payload["reason"],
            "owner": closed.payload["owner"],
            "event_id": closed.id,
        }
    return answer


def advisory_inbox(runtime, run, *, limit=20):
    from zf.runtime.durable_mailbox_authority import owner_role

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("mailbox inbox limit must be 1..100")
    credential = authenticate_role_from_environment(runtime.state_dir)
    actor = credential["role_instance"]
    if actor != owner_role(runtime.config, "orchestrator"):
        raise ValueError("this credential has no coordination inbox")
    events = MailboxIndex(runtime).read(
        {"workflow_run_id": run, "actor": actor}, select_versions=lambda *_: []
    )
    accepted = {
        e.payload["question_id"]: e.payload["message_key"]
        for e in events
        if _mail_fact(e, ACCEPTED)
        and e.payload["workflow_run_id"] == run
        and e.payload.get("question_id")
    }
    notice = notification_states(events)
    closed = {
        e.payload.get("request_id")
        for e in events
        if e.type == CLOSED and e.origin == "kernel"
    }
    rows = [
        {
            "request_id": rid,
            "message_key": accepted[rid],
            "notification": notice.get(rid, "not_dispatched"),
        }
        for e in events
        if e.type == REQUEST
        and e.origin == "kernel"
        and e.payload.get("durable_mailbox")
        and e.payload.get("owner_instance") == actor
        and e.payload.get("workflow_run_id") == run
        and (rid := e.payload["request_id"]) in accepted
        and rid not in closed
    ]
    return {"items": rows[:limit], "total": len(rows)}


def notification_states(events):
    return {
        e.payload["request_id"]: e.payload["status"]
        for e in events
        if _mail_fact(e, NOTICE)
    }


def _record_notice(runtime, request, status, **facts):
    p = request.payload
    runtime.event_writer.append(
        ZfEvent(
            type=NOTICE,
            actor=ACTOR,
            origin="kernel",
            task_id=request.task_id,
            causation_id=request.id,
            correlation_id=p["workflow_run_id"],
            payload={
                "request_id": p["request_id"],
                "question_id": p["request_id"],
                "message_key": p["message_key"],
                "owner_instance": p["owner_instance"],
                "workflow_run_id": p["workflow_run_id"],
                "status": status,
                **facts,
            },
        )
    )


def reserve_notice(runtime, request):
    """Before attempted is committed no transport call is allowed; reservations are recoverable."""
    if request.origin != "kernel" or request.actor != "run-manager":
        return False
    with locked_path(runtime.state_dir / "locks" / request.payload["request_id"]):
        events = runtime.event_log.read_all()
        status = notification_states(events).get(request.payload["request_id"])
        accepted = accepted_question(events, request.payload["request_id"])
        if accepted is None or accepted.payload["message_key"] != request.payload.get(
            "message_key"
        ):
            return False
        if status is None:
            _record_notice(runtime, request, "reserved")
        return status in {None, "reserved"}


def send_notice(runtime, request, send):
    # This lock spans the bounded transport write. A fresh process acquiring it can distinguish
    # an abandoned reservation (provably unsent) from a write with a missing receipt (unknown).
    from zf.runtime.durable_mailbox_authority import terminal

    with locked_path(runtime.state_dir / "locks" / request.payload["request_id"]):
        events = runtime.event_log.read_all()
        rid = request.payload["request_id"]
        if (
            notification_states(events).get(rid) != "reserved"
            or terminal(events, request.payload["workflow_run_id"])
            or is_run_dispatch_paused(events, request.payload["workflow_run_id"])
            or any(
                e.type == CLOSED
                and e.origin == "kernel"
                and e.payload.get("request_id") == rid
                for e in events
            )
            or question_deadline_expired(events, rid)
        ):
            return
        _record_notice(runtime, request, "attempted")
        try:
            send()
        except Exception as error:
            from zf.runtime.provider_dispatch_wait import external_gate_reason
            from zf.runtime.provider_interaction import ProviderInteractionRequired

            gate = external_gate_reason(error)
            if gate or isinstance(error, ProviderInteractionRequired):
                _record_notice(
                    runtime,
                    request,
                    "not_sent",
                    reason=gate or "provider_interaction_required",
                )
            else:
                # An exception from the adapter may follow a partial terminal write.
                reason = (
                    f"submit_withheld:{error.interactive_prompt_marker}"
                    if getattr(error, "failure_class", "") == "provider_notice_submit_withheld"
                    else "transport_receipt_absent"
                )
                _record_notice(runtime, request, "unknown", reason=reason)
            raise
        _record_notice(runtime, request, "confirmed")


def unanswered_state(runtime, request, events):
    """Resolve only message facts. Business outcomes still require the original gates."""
    p = request.payload
    if is_run_dispatch_paused(events, request.payload["workflow_run_id"]):
        return "", "dispatch_paused"
    mail = ensure_question(runtime, request, events)
    key = mail["message_key"]
    disposition = next(
        (
            e
            for e in reversed(events)
            if _mail_fact(e, "workflow.mailbox.disposition")
            and e.payload.get("message_key") == key
        ),
        None,
    )
    if disposition and disposition.payload.get("status") in {"cancelled", "timed_out"}:
        return "stale", disposition.payload["reason"]
    states = [
        e
        for e in events
        if _mail_fact(e, QUESTION) and e.payload.get("message_key") == key
    ]
    last = states[-1] if states else None
    action = last.payload.get("action") if last else ""
    if action == "answer":
        return "answered", ""
    if action == "cancel":
        return "stale", "question cancelled by authorized owner"
    if action == "wait":
        until = datetime.fromisoformat(last.payload["deadline"])
        if (
            active_request_age_seconds(
                events,
                created_at=datetime.fromisoformat(last.ts),
                now=datetime.now(timezone.utc),
                run_id=p["workflow_run_id"],
            )
            >= (until - datetime.fromisoformat(last.ts)).total_seconds()
        ):
            return (
                "waiting",
                "the authorized question wait reached its deadline without an answer",
            )
    if action == "claim" and _taker_stopped(events, last, p["owner_instance"], key):
        return (
            "waiting",
            "question taker stopped without an answer or a live authorized successor",
        )
    if question_deadline_expired(events, p["request_id"]):
        if (
            disposition
            and disposition.payload.get("status") == "evidence_unavailable"
            and not any(
                _mail_fact(event, "workflow.mailbox.acknowledged")
                and any(
                    row.get("message_key") == key
                    for row in event.payload.get("items", ())
                )
                for event in events[events.index(disposition) + 1 :]
            )
        ):
            return (
                "waiting",
                "question evidence unavailable at deadline; recovery owner: run_manager",
            )
        reason = (
            "advisory owner claimed the question without an answer"
            if action in {"claim", "wait"}
            else "advisory owner did not take the question before its deadline"
        )
        return "waiting", reason
    return "", ""


def _taker_stopped(events, claim, owner, message_key):
    """Correlate a provider close to the exact claim; unrelated idle panes have no bearing."""
    from zf.runtime.provider_turn_liveness import active_codex_turn_from_events
    from zf.runtime.workflow_operation import reduce_workflow_operations

    index = next(i for i, event in enumerate(events) if event.id == claim.id)
    consumer = claim.payload.get("consumer") or {}
    turn_id = consumer.get("provider_turn_id") or (
        active_codex_turn_from_events(events[: index + 1], owner) or {}
    ).get("turn_id")
    if not turn_id:
        return False
    closed = any(
        e.actor == owner
        and e.type in {"codex.hook.stop", "provider.turn.closed"}
        and e.payload.get("turn_id") == turn_id
        for e in events[index + 1 :]
    )
    if not closed:
        return False
    # Related successors must carry the actual question binding; unrelated work is not a handoff.
    operations = reduce_workflow_operations(events)
    linked = {
        e.payload.get("operation_id")
        for e in events
        if e.origin == "kernel"
        and e.payload.get("question_id") == claim.payload.get("question_id")
        and e.payload.get("message_key") == message_key
    }
    return not any(
        operations.get(op, {}).get("status")
        in {"requested", "reserved", "running", "suspended"}
        for op in linked
        if op
    )


def recover_interrupted_notice(runtime, request):
    """Reconcile only an abandoned write, or a proven unsent gate after an admitted Run resume."""
    rid = request.payload["request_id"]
    run = request.payload["workflow_run_id"]
    try:
        with locked_path(runtime.state_dir / "locks" / rid, timeout_seconds=0):
            events = runtime.event_log.read_all()
            last = next(
                (
                    e
                    for e in reversed(events)
                    if _mail_fact(e, NOTICE) and e.payload.get("request_id") == rid
                ),
                None,
            )
            if not last:
                return
            if last.payload["status"] == "attempted":
                _record_notice(
                    runtime, request, "unknown", reason="transport_receipt_absent"
                )
            elif last.payload["status"] == "not_sent":
                following = events[events.index(last) + 1 :]
                approved = {
                    e.causation_id
                    for e in following
                    if e.type == "runtime.action.completed"
                    and e.origin in {"", "kernel"}
                    and e.payload.get("action") == "run-resume"
                    and e.payload.get("run_id") == run
                    and e.payload.get("status") == "running"
                }
                resume = next(
                    (
                        e
                        for e in reversed(following)
                        if e.type == "run.resumed"
                        and e.id in approved
                        and e.payload.get("paused_event_id")
                        and (e.payload.get("run_id") or e.correlation_id) == run
                    ),
                    None,
                )
                if resume:
                    _record_notice(
                        runtime, request, "reserved", resume_event_id=resume.id
                    )
    except TimeoutError:
        # Another process is still inside the actual transport call.
        return
