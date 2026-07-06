"""Channel reply no-dead-end remediation (doc 79 tier applied to channels).

2026-07-03 audit: ``channel.agent.reply.failed`` had no consumer anywhere and
a dispatch that crashed after ``reply.started`` left the request permanently
blocked (started-event dedup + per-target busy gate). This module is the
deterministic Tier-1 re-arm — bounded redispatch by ``run_generation`` — plus
the Tier-2 terminal signal ``channel.agent.reply.remediation.exhausted`` that
the Run Manager surfaces as a diagnose-attention pending action.

The projection's stale-generation guard (``channel_projection._apply_reply``)
makes redispatch safe: late events from a superseded generation are dropped.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from zf.core.events import EventWriter, ZfEvent

CHANNEL_REPLY_STUCK_AFTER_SECONDS = 900
CHANNEL_REPLY_MAX_GENERATION = 3
# Requests whose last event is older than this are history, not work: without
# the cutoff, enabling remediation on a long-lived ledger would resurrect
# ancient failed replies.
CHANNEL_REPLY_MAX_AGE_SECONDS = 24 * 3600
CHANNEL_REPLY_EXHAUSTED_EVENT = "channel.agent.reply.remediation.exhausted"

_REQUESTED = "channel.agent.reply.requested"
_STATUS_BY_TYPE = {
    _REQUESTED: "pending",
    "channel.agent.reply.started": "running",
    "channel.agent.reply.completed": "completed",
    "channel.agent.reply.failed": "failed",
}


def fold_channel_reply_states(events: Iterable[ZfEvent]) -> dict[tuple[str, str], dict[str, Any]]:
    """Latest-generation state per (channel_id, request_id).

    Mirrors the projection's stale-generation rule so remediation and the
    channel projection never disagree about which run is current.
    """
    states: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        status = _STATUS_BY_TYPE.get(event.type)
        if status is None:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        channel_id = str(payload.get("channel_id") or event.correlation_id or "")
        request_id = str(payload.get("request_id") or "")
        if not channel_id or not request_id:
            continue
        item = states.setdefault((channel_id, request_id), {
            "channel_id": channel_id,
            "request_id": request_id,
            "thread_id": "main",
            "message_id": "",
            "target_member_id": "",
            "status": "",
            "run_generation": 1,
            "reason": "",
            "updated_at": "",
            "last_event_id": "",
        })
        current = _positive_int(item.get("run_generation"), default=1)
        incoming = _positive_int(payload.get("run_generation"), default=current)
        if event.type != _REQUESTED and "run_generation" in payload and incoming < current:
            continue
        item.update({
            "thread_id": str(payload.get("thread_id") or item["thread_id"] or "main"),
            "message_id": str(payload.get("message_id") or item["message_id"]),
            "target_member_id": str(payload.get("target_member_id") or item["target_member_id"]),
            "status": status,
            "run_generation": max(incoming, current) if event.type == _REQUESTED else incoming,
            "reason": str(payload.get("reason") or "") if status == "failed" else "",
            "updated_at": event.ts,
            "last_event_id": event.id,
        })
    return states


def channel_reply_remediation_candidates(
    events: list[ZfEvent],
    *,
    now: datetime | None = None,
    stuck_after_seconds: int = CHANNEL_REPLY_STUCK_AFTER_SECONDS,
    max_generation: int = CHANNEL_REPLY_MAX_GENERATION,
    max_age_seconds: int = CHANNEL_REPLY_MAX_AGE_SECONDS,
) -> list[dict[str, Any]]:
    """Stuck replies needing action: kind=redispatch (under cap) or exhaust."""
    now = now or datetime.now(timezone.utc)
    exhausted_marked: dict[tuple[str, str], int] = {}
    for event in events:
        if event.type != CHANNEL_REPLY_EXHAUSTED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        key = (str(payload.get("channel_id") or ""), str(payload.get("request_id") or ""))
        generation = _positive_int(payload.get("run_generation"), default=1)
        exhausted_marked[key] = max(exhausted_marked.get(key, 0), generation)

    out: list[dict[str, Any]] = []
    for key, item in fold_channel_reply_states(events).items():
        status = str(item.get("status") or "")
        age = _age_seconds(str(item.get("updated_at") or ""), now)
        if age is not None and age > max_age_seconds:
            continue
        if status == "failed":
            stuck = True
        elif status in {"running", "pending"}:
            stuck = age is not None and age > stuck_after_seconds
        else:
            stuck = False
        if not stuck:
            continue
        generation = _positive_int(item.get("run_generation"), default=1)
        if generation >= max_generation:
            if exhausted_marked.get(key, 0) >= generation:
                continue
            kind = "exhaust"
        else:
            kind = "redispatch"
        out.append({**item, "kind": kind})
    return out


def remediate_channel_replies(
    writer: EventWriter,
    *,
    events: list[ZfEvent],
    now: datetime | None = None,
    stuck_after_seconds: int = CHANNEL_REPLY_STUCK_AFTER_SECONDS,
    max_generation: int = CHANNEL_REPLY_MAX_GENERATION,
    dispatch: Callable[[str, str], Any] | None = None,
) -> dict[str, list[str]]:
    """Tier-1 re-arm: bounded redispatch, then one exhausted event at the cap.

    ``dispatch`` (channel_id, request_id) lets the caller attempt an immediate
    dispatch after the re-emit; a failure there is isolated per candidate —
    the re-emitted request stays pending for the reactor / next tick.
    """
    redispatched: list[str] = []
    exhausted: list[str] = []
    candidates = channel_reply_remediation_candidates(
        events,
        now=now,
        stuck_after_seconds=stuck_after_seconds,
        max_generation=max_generation,
    )
    for candidate in candidates:
        channel_id = str(candidate["channel_id"])
        request_id = str(candidate["request_id"])
        generation = _positive_int(candidate.get("run_generation"), default=1)
        if candidate["kind"] == "exhaust":
            writer.emit(
                CHANNEL_REPLY_EXHAUSTED_EVENT,
                actor="orchestrator-remediation",
                correlation_id=channel_id,
                causation_id=str(candidate.get("last_event_id") or "") or None,
                payload={
                    "channel_id": channel_id,
                    "thread_id": str(candidate.get("thread_id") or "main"),
                    "request_id": request_id,
                    "target_member_id": str(candidate.get("target_member_id") or ""),
                    "run_generation": generation,
                    "last_status": str(candidate.get("status") or ""),
                    "reason": str(candidate.get("reason") or "")
                    or f"reply stuck in {candidate.get('status')} after {generation} generations",
                    "source": "runtime",
                },
            )
            exhausted.append(request_id)
            continue
        writer.emit(
            _REQUESTED,
            actor="orchestrator-remediation",
            correlation_id=channel_id,
            causation_id=str(candidate.get("last_event_id") or "") or None,
            payload={
                "channel_id": channel_id,
                "thread_id": str(candidate.get("thread_id") or "main"),
                "request_id": request_id,
                "message_id": str(candidate.get("message_id") or ""),
                "target_member_id": str(candidate.get("target_member_id") or ""),
                "status": "pending",
                "run_generation": generation + 1,
                "routing_reason": "remediation_redispatch",
                "reason": f"remediation redispatch after {candidate.get('status')} (gen {generation})",
                "source": "runtime",
            },
        )
        redispatched.append(request_id)
        if dispatch is not None:
            try:
                dispatch(channel_id, request_id)
            except Exception:
                # Isolated on purpose: the re-emitted request is pending and
                # the reactor / next tick retries; one bad channel must not
                # starve the others.
                continue
    return {"redispatched": redispatched, "exhausted": exhausted}


def pending_channel_reply_exhausted_actions(events: list[ZfEvent]) -> list[dict[str, Any]]:
    """Tier-2: unresolved exhausted events as run-manager pending actions."""
    states = fold_channel_reply_states(events)
    latest_exhausted: dict[tuple[str, str], ZfEvent] = {}
    for event in events:
        if event.type != CHANNEL_REPLY_EXHAUSTED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        key = (str(payload.get("channel_id") or ""), str(payload.get("request_id") or ""))
        latest_exhausted[key] = event

    out: list[dict[str, Any]] = []
    for key, event in latest_exhausted.items():
        channel_id, request_id = key
        state = states.get(key, {})
        if str(state.get("status") or "") == "completed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        generation = _positive_int(payload.get("run_generation"), default=1)
        checkpoint_id = "channel-reply-exhausted-" + hashlib.sha1(
            f"{channel_id}:{request_id}:{generation}".encode("utf-8")
        ).hexdigest()[:12]
        out.append({
            "schema_version": "run-manager.pending-action.v1",
            "action": "diagnose-attention",
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": "diagnose_attention",
            "failure_class": "channel_reply_exhausted",
            "fingerprint": f"channel-reply:{channel_id}:{request_id}",
            "channel_id": channel_id,
            "request_id": request_id,
            "target_member_id": str(payload.get("target_member_id") or state.get("target_member_id") or ""),
            "run_generation": generation,
            "source_event_id": event.id,
            "source_event_type": event.type,
            "source_event_ids": [event.id] if event.id else [],
            "owner_route": "run_manager",
            "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose",
            "route_registry": "run-manager-router.v1",
            "reason": (
                "channel agent reply exhausted bounded redispatch "
                f"(gen {generation}); needs diagnosis"
            ),
        })
    return out


def _age_seconds(ts: str, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds()


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
