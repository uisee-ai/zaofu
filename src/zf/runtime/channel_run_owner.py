"""Deterministic ProviderRun ownership helpers for Agent Channel."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable


RUN_ACTIVE_STATUSES = frozenset({"pending", "running", "started"})
RUN_BUSY_STATUSES = frozenset({"running", "started"})
RUN_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "stale"})


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
        if str(item.get("status") or "") in wanted:
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
