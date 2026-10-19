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
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from zf.core.events import EventWriter, ZfEvent
from zf.runtime.channel_reply_queue import current_review_reply_bindings
from zf.runtime.channel_cross_review_outcomes import rejected_review_reply
from zf.runtime.channel_run_owner import stale_reply_transition

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

_NON_RETRYABLE_FAILURE_STATUSES = frozenset({
    "command_unavailable",
    "incomplete",
    "delivery_unknown",
    "result_unreadable",
    "permission_denied",
    "permission_drift_blocked",
    "provider_binding_missing",
    "sandbox_unsupported",
    "target_member_inactive",
})
_NON_RETRYABLE_REASON_MARKERS = (
    "command is unavailable",
    "permission drift",
    "provider binding is missing",
    "sandbox cannot be created",
    "sandbox_unsupported",
    "target_member_inactive",
    "unshare",
)

_FAILURE_CATEGORIES = {
    "failed": "failed",
    "cancelled": "cancelled",
    "timeout": "timeout",
    "rate_limited": "rate_limit",
    "dispatch_exception": "dispatch",
    "contract_invalid": "contract",
    "incomplete": "provider",
}


def classify_channel_reply_failure(
    *,
    status: object = "",
    reason: object = "",
    retryable: object = None,
) -> dict[str, Any]:
    """Classify provider failures before the bounded redispatch policy."""

    normalized_status = str(status or "failed").strip().lower()
    normalized_reason = str(reason or "").strip()
    reason_lower = normalized_reason.lower()
    superseded = "superseded by" in reason_lower
    if isinstance(retryable, bool):
        is_retryable = retryable
    else:
        is_retryable = not (
            superseded
            or normalized_status in _NON_RETRYABLE_FAILURE_STATUSES
            or any(marker in reason_lower for marker in _NON_RETRYABLE_REASON_MARKERS)
        )
    category = _FAILURE_CATEGORIES.get(normalized_status, "provider")
    if superseded:
        category = "superseded"
        is_retryable = False
    elif normalized_status == "cancelled":
        # Operator cancellation is an intentional terminal decision, not a
        # transient provider failure that the watchdog may resurrect.
        is_retryable = False
    failure_class = (
        "channel_reply_superseded"
        if superseded
        else "channel_contribution_contract_invalid"
        if normalized_status == "contract_invalid"
        else f"provider_{normalized_status.replace('-', '_')}"
    )
    return {
        "failure_status": normalized_status,
        "failure_class": failure_class,
        "retryable": is_retryable,
        "terminal_disposition": "superseded" if superseded else "failed",
        "failure_category": category,
    }


def emit_channel_reply_failed(
    *,
    writer: EventWriter,
    request: dict[str, Any],
    request_id: str,
    started_event_id: str,
    actor: str,
    source: str,
    channel_id: str,
    reason: str,
    provider_session_id: str,
    failure_status: str = "failed",
) -> None:
    """Emit one classified provider failure and close a bound handoff."""

    from zf.runtime.channel_run_owner import provider_run_fields_for_request

    writer.emit(
        "channel.agent.reply.failed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=started_event_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": str(request.get("thread_id") or "main"),
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "provider_session_id": provider_session_id,
            "reason": reason,
            **classify_channel_reply_failure(status=failure_status, reason=reason),
            **provider_run_fields_for_request(channel_id, request),
            "source": source,
        },
    )
    handoff_request_event_id = str(request.get("handoff_request_event_id") or "")
    if not handoff_request_event_id:
        return
    writer.emit(
        "channel.handoff.failed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=handoff_request_event_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": str(request.get("thread_id") or "main"),
            "message_id": str(request.get("message_id") or ""),
            "member_id": str(request.get("member_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "request_id": request_id,
            "handoff_request_event_id": handoff_request_event_id,
            "reason": reason,
            "source": source,
        },
    )


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
            "failure_status": "",
            "failure_class": "",
            "retryable": True,
            "terminal_disposition": "",
            "updated_at": "",
            "last_event_id": "",
        })
        current = _positive_int(item.get("run_generation"), default=1)
        incoming = _positive_int(payload.get("run_generation"), default=current)
        if stale_reply_transition(item, generation=incoming, status=status):
            continue
        if event.type == _REQUESTED and incoming > current:
            item.update({
                "failure_status": "",
                "failure_class": "",
                "failure_category": "",
                "terminal_disposition": "",
                "retryable": True,
                "provider_timeout_seconds": 0,
            })
        failure = (
            classify_channel_reply_failure(
                status=payload.get("failure_status") or payload.get("status"),
                reason=payload.get("reason"),
                retryable=payload.get("retryable"),
            )
            if status == "failed"
            else {}
        )
        if event.type == "channel.agent.reply.started":
            item["provider_timeout_seconds"] = payload.get("provider_timeout_seconds", 0)
        item.update({
            "thread_id": str(payload.get("thread_id") or item["thread_id"] or "main"),
            "message_id": str(payload.get("message_id") or item["message_id"]),
            "target_member_id": str(payload.get("target_member_id") or item["target_member_id"]),
            "status": status,
            "run_generation": max(incoming, current) if event.type == _REQUESTED else incoming,
            "reason": str(payload.get("reason") or "") if status == "failed" else "",
            "updated_at": event.ts,
            "last_event_id": event.id,
            **failure,
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
    recoverable = current_review_reply_bindings(events)
    rejected = current_review_reply_bindings(events, status="rejected")
    for key, item in fold_channel_reply_states(events).items():
        status = str(item.get("status") or "")
        age = _age_seconds(str(item.get("updated_at") or ""), now)
        if age is not None and age > max_age_seconds:
            continue
        rejection = rejected.get(key)
        if status == "completed" and rejection is not None:
            previous = rejected_review_reply(events, rejection)
            if (previous.get("request_id") == key[1]
                    and rejection.causation_id == item.get("last_event_id")):
                status = "failed"
                item = {**item, "status": status, "retryable": True, "queue_recovery": True,
                        "reason": str(rejection.payload.get("reason") or ""),
                        "failure_status": "contract_invalid",
                        "failure_class": "channel_cross_review_contract_invalid"}
        if status == "failed":
            if str(item.get("terminal_disposition") or "") == "superseded":
                if key not in recoverable or item.get("reason") != "superseded by latest queued mention":
                    continue
                item = {**item, "retryable": True, "queue_recovery": True}

            stuck = True
        elif status in {"running", "pending"}:
            limit = stuck_after_seconds
            budget = item.get("provider_timeout_seconds")
            if (status == "running" and isinstance(budget, (int, float))
                    and not isinstance(budget, bool) and math.isfinite(budget) and budget > 0):
                limit = max(limit, budget + 60)  # Provider termination/receipt grace.
            stuck = age is not None and age > limit
        else:
            stuck = False
        if not stuck:
            continue
        if str(item.get("failure_status") or "") == "rate_limited":
            retry_after = _retry_after_seconds(str(item.get("reason") or ""))
            if retry_after is not None and (age is None or age < retry_after):
                continue
        if status == "running":
            item = {**item, "retryable": False, "failure_status": "delivery_unknown",
                    "reason": "provider delivery outcome unknown; inspect the original run before retry"}
        generation = _positive_int(item.get("run_generation"), default=1)
        if not bool(item.get("retryable", True)):
            if exhausted_marked.get(key, 0) >= generation:
                continue
            kind = "exhaust"
        elif generation >= max_generation:
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
            # A stale pending/running request has no terminal provider event.
            # Close it before emitting the diagnostic, otherwise the UI and
            # cold-resume sweep keep treating it as live forever.
            if str(candidate.get("status") or "") in {"pending", "running"}:
                emit_channel_reply_failed(
                    writer=writer,
                    request=candidate,
                    request_id=request_id,
                    started_event_id=str(candidate.get("last_event_id") or ""),
                    actor="orchestrator-remediation",
                    source="runtime:timeout",
                    channel_id=channel_id,
                    reason=(
                        f"channel reply timed out after bounded retries "
                        f"(generation {generation}); {candidate.get('reason') or ''}"
                    ),
                    provider_session_id=str(candidate.get("provider_session_id") or ""),
                    failure_status=str(candidate.get("failure_status") or "timeout"),
                )
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
                    "failure_status": str(candidate.get("failure_status") or ""),
                    "failure_class": str(candidate.get("failure_class") or ""),
                    "retryable": bool(candidate.get("retryable", True)),
                    "source": "runtime",
                },
            )
            exhausted.append(request_id)
            continue
        prior_reason = str(candidate.get("reason") or "").strip()
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
                "reason": (
                    f"remediation redispatch after {candidate.get('status')} "
                    f"(gen {generation})"
                    + (f"; diagnostic: {prior_reason}" if prior_reason else "")
                ),
                "source": "runtime",
            },
        )
        redispatched.append(request_id)
        if dispatch is not None and not candidate.get("queue_recovery"):
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
            "failure_status": str(payload.get("failure_status") or ""),
            "provider_failure_class": str(payload.get("failure_class") or ""),
            "retryable": bool(payload.get("retryable", True)),
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


def drain_terminal_channel_reply(
    runtime: Any,
    event: ZfEvent,
    channel_id: str,
) -> None:
    if event.type not in {
        "channel.agent.reply.completed",
        "channel.agent.reply.failed",
    }:
        return
    from zf.runtime.channel_message_ingress import (
        dispatch_channel_replies_background,
    )

    dispatch_channel_replies_background(
        state_dir=runtime.state_dir,
        channel_id=channel_id,
        actor="orchestrator-reactor",
        source="runtime:terminal-drain",
        project_root=getattr(runtime, "project_root", None),
        config=getattr(runtime, "config", None),
        openclaw_client=getattr(runtime, "openclaw_client", None),
        max_dispatch=64,
    )


def resume_pending_channel_reply_queues(
    runtime: Any,
    events: list[ZfEvent],
) -> None:
    channel_ids = {
        str((event.payload or {}).get("channel_id") or event.correlation_id or "")
        for event in events
        if event.type == _REQUESTED
        and isinstance(event.payload, dict)
        and str(event.payload.get("status") or "") in {"pending", "queued"}
    }
    if not channel_ids:
        return
    from zf.runtime.channel_message_ingress import (
        dispatch_channel_replies_background,
    )
    from zf.runtime.channel_projection import project_channel

    for channel_id in sorted(item for item in channel_ids if item):
        channel = project_channel(runtime.state_dir, channel_id) or {}
        if not any(
            isinstance(item, dict)
            and str(item.get("status") or "") in {"pending", "queued"}
            for item in channel.get("reply_requests") or []
        ):
            continue
        dispatch_channel_replies_background(
            state_dir=runtime.state_dir,
            channel_id=channel_id,
            actor="orchestrator-remediation",
            source="runtime:cold-resume",
            project_root=getattr(runtime, "project_root", None),
            config=getattr(runtime, "config", None),
            openclaw_client=getattr(runtime, "openclaw_client", None),
            max_dispatch=64,
        )


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


def _retry_after_seconds(reason: str) -> int | None:
    match = re.search(r"retry\s+after\s+(\d+)\s*s?", reason.lower())
    if not match:
        return None
    return max(int(match.group(1)), 0)


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def check_channel_reply_remediation(runtime: Any) -> None:
    """Recover durable intents, remediate replies, then drain their queues."""
    from zf.runtime.channel_dedup_recovery import resume_unstarted_question_dedup
    from zf.runtime.channel_handoff_recovery import resume_unstarted_channel_handoffs
    from zf.runtime.channel_result_recovery import resume_unapplied_channel_results

    try:
        events = runtime.event_log.read_all()
    except Exception:
        return
    resume_unstarted_question_dedup(runtime, events)
    resume_unstarted_channel_handoffs(runtime, events)
    resume_unapplied_channel_results(runtime, events)
    events = runtime.event_log.read_all()
    if not any(event.type.startswith("channel.agent.reply.") for event in events):
        return
    remediate_channel_replies(runtime.event_writer, events=events)
    resume_pending_channel_reply_queues(runtime, runtime.event_log.read_all())
