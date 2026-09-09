"""Mechanical reactor for a Synthesizer-requested selective Channel pass."""

from __future__ import annotations

import hashlib

from zf.core.events import ZfEvent
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_sidecar import channel_message_event_payload


def react_channel_synthesis_next_round_proposed(
    host,
    event: ZfEvent,
) -> None:
    """Apply one sidecar-backed selective pass proposed by a Synthesizer.

    The proposal carries agent judgment. This reactor only verifies current
    event/session identity and roster membership, then reuses the ordinary
    Channel message -> router -> reply-request path.
    """

    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = str(payload.get("channel_id") or event.correlation_id or "")
    thread_id = str(payload.get("thread_id") or "main")
    synthesis_event_id = str(payload.get("synthesis_event_id") or "")
    synthesis_request_id = str(payload.get("synthesis_request_id") or "")
    if not channel_id or not synthesis_event_id or not synthesis_request_id:
        return
    events = host.event_log.read_all()
    prior_continued = next(
        (
            prior
            for prior in events
            if prior.type == "channel.discussion.continued"
            and isinstance(prior.payload, dict)
            and str(prior.payload.get("synthesis_event_id") or "")
            == synthesis_event_id
        ),
        None,
    )
    if prior_continued is not None:
        _route_adaptive_next_round_message(
            host,
            event,
            payload,
            continued_payload=prior_continued.payload,
        )
        return

    source_synthesis = next(
        (
            prior
            for prior in events
            if prior.id == synthesis_event_id
            and prior.type == "channel.synthesis.proposed"
        ),
        None,
    )
    if source_synthesis is None:
        _reject_next_round(host, event, "synthesis_event_missing")
        return
    synthesis_payload = (
        source_synthesis.payload
        if isinstance(source_synthesis.payload, dict)
        else {}
    )
    declared = (
        synthesis_payload.get("next_round")
        if isinstance(synthesis_payload.get("next_round"), dict)
        else {}
    )
    requested_targets = _string_list(payload.get("target_member_ids"))
    if (
        str(synthesis_payload.get("request_id") or "") != synthesis_request_id
        or str(declared.get("action") or "") != "continue"
        or str(declared.get("reason") or "") != str(payload.get("reason") or "")
        or str(declared.get("objective") or "")
        != str(payload.get("objective") or "")
        or requested_targets != _string_list(declared.get("target_member_ids"))
    ):
        _reject_next_round(host, event, "synthesis_next_round_mismatch")
        return

    channel = project_channel(host.state_dir, channel_id) or {}
    sessions = channel.get("discussions")
    session = sessions.get(thread_id) if isinstance(sessions, dict) else {}
    if not isinstance(session, dict):
        _reject_next_round(host, event, "discussion_session_missing")
        return
    current_revision = int(session.get("revision") or 0)
    current_context_digest = str(session.get("context_digest") or "")
    if str(session.get("state") or "") != "phase3_synthesis":
        _reject_next_round(
            host,
            event,
            "discussion_not_waiting_for_synthesis",
            current_revision=current_revision,
            current_context_digest=current_context_digest,
        )
        return
    if str(session.get("discussion_id") or "") != str(
        payload.get("discussion_id") or ""
    ):
        _reject_next_round(
            host,
            event,
            "discussion_identity_stale",
            current_revision=current_revision,
            current_context_digest=current_context_digest,
        )
        return
    if int(payload.get("expected_revision") or 0) != current_revision:
        _reject_next_round(
            host,
            event,
            "discussion_revision_stale",
            current_revision=current_revision,
            current_context_digest=current_context_digest,
        )
        return
    if str(payload.get("expected_context_digest") or "") != current_context_digest:
        _reject_next_round(
            host,
            event,
            "discussion_context_stale",
            current_revision=current_revision,
            current_context_digest=current_context_digest,
        )
        return
    if _has_open_owner_questions(channel, thread_id):
        _reject_next_round(
            host,
            event,
            "owner_questions_open",
            current_revision=current_revision,
            current_context_digest=current_context_digest,
        )
        return
    roster = _string_list(session.get("roster"))
    active_members = {
        str(member.get("member_id") or "")
        for member in channel.get("members") or []
        if isinstance(member, dict)
        and str(member.get("status") or "").lower()
        not in {"removed", "suspended", "rejected", "failed"}
    }
    if not requested_targets or not set(requested_targets) <= set(roster):
        _reject_next_round(
            host,
            event,
            "next_round_targets_not_in_current_roster",
            current_revision=current_revision,
            current_context_digest=current_context_digest,
        )
        return
    if not set(requested_targets) <= active_members:
        _reject_next_round(
            host,
            event,
            "next_round_targets_not_active",
            current_revision=current_revision,
            current_context_digest=current_context_digest,
        )
        return

    next_revision = current_revision + 1
    discussion_id = str(session.get("discussion_id") or "")
    next_round_id = "next-round-" + hashlib.sha1(
        f"{event.id}:{discussion_id}:{next_revision}".encode("utf-8")
    ).hexdigest()[:16]
    message_id = f"msg-{next_round_id}"
    next_context_digest = hashlib.sha256(
        (
            f"{discussion_id}:{next_revision}:{synthesis_event_id}:"
            f"{payload.get('objective') or ''}:{','.join(requested_targets)}"
        ).encode("utf-8")
    ).hexdigest()
    continued = host.event_writer.emit(
        "channel.discussion.continued",
        actor="orchestrator-reactor",
        task_id=event.task_id,
        causation_id=event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "discussion_id": discussion_id,
            "revision": next_revision,
            "context_digest": next_context_digest,
            "product_mode": str(session.get("product_mode") or ""),
            "roster": requested_targets,
            # Keep the original requirement stable across focused passes. The
            # adaptive message is a bounded contribution request, not a new
            # product requirement and must not replace PRD provenance.
            "requirement_message_id": str(
                session.get("requirement_message_id") or ""
            ),
            "adaptive_next_round_message_id": message_id,
            "synthesis_event_id": synthesis_event_id,
            "synthesis_request_id": synthesis_request_id,
            "next_round_id": next_round_id,
            "reason": str(payload.get("reason") or ""),
            "target_member_ids": requested_targets,
            "source": "runtime",
        },
    )
    host.event_writer.emit(
        "channel.discussion.phase.changed",
        actor="orchestrator-reactor",
        task_id=event.task_id,
        causation_id=continued.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "phase": "phase1_blind",
            "reason": "synthesis_next_round",
            "source": "runtime",
        },
    )
    _route_adaptive_next_round_message(
        host,
        event,
        payload,
        continued_payload=continued.payload,
        next_round_id=next_round_id,
    )


def _route_adaptive_next_round_message(
    host,
    event: ZfEvent,
    payload: dict,
    *,
    continued_payload: dict | None,
    next_round_id: str = "",
) -> None:
    continued = continued_payload if isinstance(continued_payload, dict) else {}
    channel_id = str(payload.get("channel_id") or event.correlation_id or "")
    thread_id = str(payload.get("thread_id") or "main")
    synthesis_event_id = str(payload.get("synthesis_event_id") or "")
    next_round_id = str(continued.get("next_round_id") or next_round_id or "")
    next_round_id = next_round_id or "next-round-" + hashlib.sha1(
        synthesis_event_id.encode("utf-8")
    ).hexdigest()[:16]
    message = _message_for_ref(
        host,
        key="adaptive_next_round_id",
        value=next_round_id,
    )
    targets = _string_list(
        continued.get("target_member_ids")
        or continued.get("roster")
        or payload.get("target_member_ids")
    )
    if message is None:
        mentions = " ".join(f"@{member_id}" for member_id in targets)
        message_payload = channel_message_event_payload(
            host.state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": str(
                    continued.get("adaptive_next_round_message_id")
                    # Compatibility for events persisted before the dedicated
                    # field: they used requirement_message_id for this message.
                    or continued.get("requirement_message_id")
                    or f"msg-{next_round_id}"
                ),
                "member_id": "operator",
                "role": "user",
                "source": "runtime",
                "text": (
                    f"{mentions} Selective next pass. Objective: "
                    f"{str(payload.get('objective') or '').strip()}"
                ).strip(),
                "mentions": targets,
                "refs": {
                    "adaptive_next_round_id": next_round_id,
                    # These identify the completed parent operation. They are
                    # deliberately not the reserved current synthesis refs:
                    # this message is a member contribution request.
                    "originating_synthesis_event_id": synthesis_event_id,
                    "originating_synthesis_request_id": str(
                        payload.get("synthesis_request_id") or ""
                    ),
                    "discussion_id": str(
                        continued.get("discussion_id")
                        or payload.get("discussion_id")
                        or ""
                    ),
                    "discussion_revision": int(
                        continued.get("revision") or 0
                    ),
                    "discussion_context_digest": str(
                        continued.get("context_digest") or ""
                    ),
                },
            },
            created_by="channel-adaptive-next-round:runtime",
            source_event_id=event.id,
        )
        message = host.event_writer.emit(
            "channel.message.posted",
            actor="orchestrator-reactor",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=channel_id,
            payload=message_payload,
        )
    _route_runtime_message(host, event, message)


def _has_open_owner_questions(channel: dict, thread_id: str) -> bool:
    raw_questions = channel.get("open_questions") or []
    questions = (
        raw_questions.values()
        if isinstance(raw_questions, dict)
        else raw_questions
    )
    return any(
        isinstance(question, dict)
        and str(question.get("thread_id") or "main") == thread_id
        and str(question.get("status") or "") == "open"
        for question in questions
    )


def _reject_next_round(
    host,
    event: ZfEvent,
    reason: str,
    *,
    current_revision: int = 0,
    current_context_digest: str = "",
) -> None:
    if any(
        prior.type == "channel.discussion.next_round.rejected"
        and prior.causation_id == event.id
        for prior in host.event_log.read_all()
    ):
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    host.event_writer.emit(
        "channel.discussion.next_round.rejected",
        actor="orchestrator-reactor",
        task_id=event.task_id,
        causation_id=event.id,
        correlation_id=str(payload.get("channel_id") or event.correlation_id or ""),
        payload={
            "channel_id": str(payload.get("channel_id") or ""),
            "thread_id": str(payload.get("thread_id") or "main"),
            "synthesis_event_id": str(payload.get("synthesis_event_id") or ""),
            "synthesis_request_id": str(
                payload.get("synthesis_request_id") or ""
            ),
            "discussion_id": str(payload.get("discussion_id") or ""),
            "expected_revision": int(payload.get("expected_revision") or 0),
            "current_revision": current_revision,
            "expected_context_digest": str(
                payload.get("expected_context_digest") or ""
            ),
            "current_context_digest": current_context_digest,
            "target_member_ids": _string_list(payload.get("target_member_ids")),
            "reason": reason,
            "source": "runtime",
        },
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _message_for_ref(host, *, key: str, value: str):
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        refs = (
            prior_payload.get("refs")
            if isinstance(prior_payload.get("refs"), dict)
            else {}
        )
        if (
            prior.type == "channel.message.posted"
            and str(refs.get(key) or "") == value
        ):
            return prior
    return None


def _route_runtime_message(host, event: ZfEvent, message: ZfEvent) -> None:
    message_id = str((message.payload or {}).get("message_id") or "")
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        if (
            prior.type == "channel.agent.reply.requested"
            and str(prior_payload.get("message_id") or "") == message_id
        ):
            return
    route_channel_message(
        state_dir=host.state_dir,
        writer=host.event_writer,
        message_event=message,
        message_payload=message.payload,
        actor="orchestrator-reactor",
        source="runtime",
        project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None),
        openclaw_client=getattr(host, "openclaw_client", None),
        dispatch_inline=True,
    )


__all__ = ["react_channel_synthesis_next_round_proposed"]
