"""Deterministic ProviderRun ownership helpers for Agent Channel."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable


RUN_ACTIVE_STATUSES = frozenset({"pending", "running", "started"})
RUN_BUSY_STATUSES = frozenset({"running", "started"})
RUN_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "stale"})


def stale_reply_transition(
    current: dict[str, Any], *, generation: int, status: str,
) -> bool:
    """Share terminal/generation ordering between UI and recovery folds.

    A completed transport may still receive a contract failure verdict.
    Preserve failure updates, but require a new generation to resume work.
    """
    current_generation = _positive_int(current.get("run_generation"), default=1)
    return generation < current_generation or (
        generation == current_generation
        and current.get("status") in RUN_TERMINAL_STATUSES
        and status != "failed"
    )


def stable_provider_run_id(
    *,
    channel_id: str,
    thread_id: str,
    request_id: str,
    target_member_id: str,
) -> str:
    digest = hashlib.sha1(
        f"{channel_id}:{thread_id or 'main'}:{request_id}:{target_member_id}".encode("utf-8"),
    ).hexdigest()[:16]
    return f"run-{digest}"


def provider_run_fields(
    *,
    channel_id: str,
    thread_id: str,
    request_id: str,
    target_member_id: str,
    run_generation: int | str = 1,
) -> dict[str, Any]:
    generation = _positive_int(run_generation, default=1)
    run_id = stable_provider_run_id(
        channel_id=channel_id,
        thread_id=thread_id or "main",
        request_id=request_id,
        target_member_id=target_member_id,
    )
    return {
        "run_id": run_id,
        "provider_run_id": run_id,
        "run_generation": generation,
    }


def provider_run_fields_for_request(
    channel_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    return provider_run_fields(
        channel_id=channel_id,
        thread_id=str(request.get("thread_id") or "main"),
        request_id=str(request.get("request_id") or ""),
        target_member_id=str(request.get("target_member_id") or ""),
        run_generation=request.get("run_generation") or 1,
    )


def reply_generation_is_current(
    events: Iterable[Any], *, channel_id: str, request: dict[str, Any],
) -> bool:
    """Only a requested generation may publish the current reply body/contract."""
    request_id = str(request.get("request_id") or "")
    if not request_id:
        return True
    generations = [
        _positive_int(event.payload.get("run_generation"), default=1)
        for event in events
        if event.type == "channel.agent.reply.requested"
        and event.payload.get("channel_id") == channel_id
        and event.payload.get("request_id") == request_id
    ]
    return not generations or _positive_int(
        request.get("run_generation"), default=1
    ) == max(generations)


def active_reply_for_target(
    channel: dict[str, Any],
    target_member_id: str,
    *,
    exclude_request_id: str = "",
    statuses: Iterable[str] = RUN_BUSY_STATUSES,
) -> dict[str, Any]:
    wanted = {str(status) for status in statuses}
    for item in channel.get("reply_requests") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("target_member_id") or "") != target_member_id:
            continue
        if exclude_request_id and str(item.get("request_id") or "") == exclude_request_id:
            continue
        if str(item.get("status") or "") in wanted or (
            item.get("status") == "failed" and item.get("failure_status") == "delivery_unknown"
        ):
            return item
    return {}


def provider_run_record(reply: dict[str, Any], *, channel_id: str = "") -> dict[str, Any]:
    thread_id = str(reply.get("thread_id") or "main")
    request_id = str(reply.get("request_id") or reply.get("event_id") or "")
    target_member_id = str(reply.get("target_member_id") or "")
    run_id = str(reply.get("run_id") or reply.get("provider_run_id") or "")
    if not run_id and channel_id and request_id and target_member_id:
        run_id = stable_provider_run_id(
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            target_member_id=target_member_id,
        )
    status = str(reply.get("status") or "")
    return {
        "run_id": run_id,
        "provider_run_id": run_id,
        "run_generation": _positive_int(reply.get("run_generation"), default=1),
        "request_id": request_id,
        "thread_id": thread_id,
        "message_id": str(reply.get("message_id") or ""),
        "target_member_id": target_member_id,
        "member_id": str(reply.get("member_id") or ""),
        "status": status,
        "provider": str(reply.get("provider") or ""),
        "backend": str(reply.get("backend") or ""),
        "provider_session_id": str(reply.get("provider_session_id") or ""),
        "context_pack_id": str(reply.get("context_pack_id") or ""),
        "reason": str(reply.get("reason") or ""),
        "created_at": str(reply.get("created_at") or ""),
        "updated_at": str(reply.get("updated_at") or reply.get("created_at") or ""),
    }


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


_PHASE_SESSION_REF_KEYS = (
    "adaptive_next_round_id",
    "synthesis_repair_id",
    "question_dedup_request_id",
    "synthesis_request_id",
    "cross_review_request_id",
    "consensus_review_id",
)


def channel_provider_thread_key(
    *,
    channel_id: str,
    request: dict[str, Any],
    message: dict[str, Any],
    blind_contribution_id: str = "",
) -> str:
    """Return the durable provider-thread key for one Channel reply.

    Conversational contributions retain a member-scoped provider session.  A
    typed deliberation phase is different: it receives an authoritative,
    complete context pack and must be isolated from prior agent tool history.
    Synthesis repairs have their own identity and authoritative context so a
    new repair's generation 1 cannot resume an earlier failed synthesis turn.
    Continuations retain the session; transport retry generations start fresh.
    """
    thread_id = str(request.get("thread_id") or "main")
    member_id = str(request.get("target_member_id") or "")
    base = f"channel:{channel_id}:{thread_id}:{member_id}"
    if blind_contribution_id:
        generation = provider_run_fields_for_request(channel_id, request)["run_generation"]
        return f"{base}:phase:blind_contribution:{blind_contribution_id}:attempt:{generation}"
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    for ref_key in _PHASE_SESSION_REF_KEYS:
        phase_id = str(refs.get(ref_key) or "").strip()
        if phase_id:
            generation = provider_run_fields_for_request(channel_id, request)["run_generation"]
            suffix = f":attempt:{generation}" if generation > 1 else ""
            return f"{base}:phase:{ref_key}:{phase_id}{suffix}"
    return base
