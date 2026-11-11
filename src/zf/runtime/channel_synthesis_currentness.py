"""Bind synthesis publication to the latest durable request and discussion."""
from __future__ import annotations

from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.runtime.channel_synthesis_lock import prior_synthesis_questions_are_resolved


def current_synthesis_consensus(channel: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Older PRD verdicts cannot drive a newer synthesis generation."""
    consensus = (channel.get("consensus") or {}).get(thread_id) or {}
    requests = [r for r in channel.get("synthesis_requests") or []
                if str(r.get("thread_id") or "main") == thread_id]
    syntheses = [r for r in channel.get("syntheses") or []
                 if str(r.get("thread_id") or "main") == thread_id]
    if syntheses:
        latest = syntheses[-1]
        if requests and requests[-1].get("synthesis_event_id") != latest.get("event_id"):
            return {}
        if any(latest.get(key) and latest.get(key) != consensus.get(key)
               for key in ("artifact_ref", "artifact_digest", "prd_revision")):
            return {}
    return consensus


def synthesis_request_currentness(
    events: list[ZfEvent], *, channel_id: str, thread_id: str,
    request_id: str, session: dict[str, Any],
) -> dict[str, str] | None:
    """None means legacy/untracked; empty means current; otherwise explain staleness."""
    scoped = [(index, event) for index, event in enumerate(events)
              if event.payload.get("channel_id") == channel_id
              and str(event.payload.get("thread_id") or "main") == thread_id]
    requests = [(i, e) for i, e in scoped if e.type == "channel.synthesis.requested"]
    target = next(((i, e) for i, e in reversed(requests)
                   if e.payload.get("request_id") == request_id), None)
    if target is None:
        return None
    index, event = target
    boundary = max((i for i, e in scoped if e.type in {
        "channel.discussion.started", "channel.discussion.continued",
    }), default=-1)
    latest_index, latest = requests[-1]
    payload = event.payload
    reason = ""
    if any(e.type == "channel.synthesis.cancelled" and e.payload.get("request_id") == request_id
           for _, e in scoped):
        reason = "synthesis_cancelled"
    elif index < boundary:
        reason = "discussion_advanced_after_synthesis_request"
    elif index != latest_index:
        reason = "newer_synthesis_request_exists"
    elif payload.get("discussion_revision") is not None and int(payload["discussion_revision"]) != int(session.get("revision") or 1):
        reason = "discussion_revision_changed"
    elif payload.get("discussion_context_digest") is not None and str(payload["discussion_context_digest"]) != str(session.get("context_digest") or ""):
        reason = "discussion_context_changed"
    if not reason:
        return {}
    return {"reason": reason, "superseded_by_request_id": str(latest.payload.get("request_id") or ""),
            "superseded_by_event_id": latest.id}


def ignore_superseded_synthesis_reply(
    *,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    synthesis_request_id: str,
    reply_event_id: str,
    task_id: str,
    source: str,
) -> bool:
    """Record and ignore a late distinct synthesis for the current PRD."""
    discussions = channel.get("discussions")
    session = discussions.get(thread_id, {}) if isinstance(discussions, dict) else {}
    events = writer.event_log.read_all()
    if any(event.type == "channel.synthesis.proposed"
           and event.payload.get("channel_id") == channel_id
           and str(event.payload.get("thread_id") or "main") == thread_id
           and event.payload.get("request_id") == synthesis_request_id for event in events):
        return True
    currentness = synthesis_request_currentness(
        events, channel_id=channel_id, thread_id=thread_id,
        request_id=synthesis_request_id, session=session,
    )
    if currentness is not None:
        if not currentness:
            return False
        if not any(event.type == "channel.synthesis.stale_ignored"
                   and event.payload.get("source_reply_event_id") == reply_event_id for event in events):
            writer.emit("channel.synthesis.stale_ignored", actor="channel-contract",
                        task_id=task_id, causation_id=reply_event_id, correlation_id=channel_id,
                        payload={"channel_id": channel_id, "thread_id": thread_id,
                                 "request_id": synthesis_request_id, "source_reply_event_id": reply_event_id,
                                 "source": source, **currentness})
        return True
    if (
        str(session.get("state") or "") == "phase2_relay"
        and str(session.get("phase_reason") or "")
        in {"synthesis_questions_opened", "consensus_blocked"}
    ):
        return False
    started_event_id = str(session.get("started_event_id") or "")
    started = not started_event_id
    prior = None
    for event in events:
        if event.id == started_event_id:
            started = True
            continue
        if not started or event.type != "channel.synthesis.proposed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("channel_id") or "") == channel_id
            and str(payload.get("thread_id") or "main") == thread_id
            and str(payload.get("request_id") or "") != synthesis_request_id
        ):
            prior = event
    if prior is None:
        return False
    if prior_synthesis_questions_are_resolved(
        channel=channel,
        prior_synthesis_event=prior,
        events=events,
    ):
        # A prior draft may deliberately reopen the discussion with Owner
        # questions. Once those questions are resolved, the next generated
        # synthesis is a new revision, not a late competing reply.
        return False
    if any(
        event.type == "channel.synthesis.stale_ignored"
        and isinstance(event.payload, dict)
        and str(event.payload.get("source_reply_event_id") or "")
        == reply_event_id
        for event in events
    ):
        return True
    prior_payload = prior.payload if isinstance(prior.payload, dict) else {}
    writer.emit(
        "channel.synthesis.stale_ignored",
        actor="zf-kernel",
        task_id=task_id or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "schema_version": "channel.synthesis.stale.v1",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": synthesis_request_id,
            "superseded_by_request_id": str(
                prior_payload.get("request_id") or ""
            ),
            "canonical_artifact_ref": str(
                prior_payload.get("artifact_ref") or ""
            ),
            "canonical_artifact_digest": str(
                prior_payload.get("artifact_digest") or ""
            ),
            "source_reply_event_id": reply_event_id,
            "reason": "prior_synthesis_proposed",
            "source": source,
        },
    )
    return True
