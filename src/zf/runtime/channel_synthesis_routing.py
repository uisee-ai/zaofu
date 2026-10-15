"""Preserve rejected synthesis attempts without treating them as running work."""
from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent


def mark_rejected_synthesis(channel: dict[str, Any], event: ZfEvent) -> None:
    """Replay existing context rejection evidence, including pre-fix requests."""
    payload = event.payload
    message_id = str(payload.get("trigger_message_id") or "")
    message = channel["messages"].get(message_id, {})
    synthesis_id = (message.get("refs") or {}).get("synthesis_request_id")
    if not synthesis_id:
        return
    for request in channel["synthesis_requests"]:
        if (request.get("request_id") == synthesis_id
            and request.get("status") in {"requested", "repair_requested"}
            and request.get("target_member_id") == payload.get("target_member_id")
            and request.get("thread_id") == str(payload.get("thread_id") or "main")):
            request.update(status="blocked", recovery_message_id=message_id,
                           context_rejection_event_id=event.id,
                           reason=str(payload.get("reason") or "context_pack_rejected"),
                           updated_at=event.ts)


def retired_synthesis_message_ids(channel: dict[str, Any], thread_id: str) -> set[str]:
    """A new explicit attempt supersedes only its own historic routing errors."""
    requests = [r for r in channel.get("synthesis_requests", [])
                if str(r.get("thread_id") or "main") == thread_id]
    latest = requests[-1].get("request_id") if requests else None
    return {str(r["recovery_message_id"]) for r in requests
            if r.get("request_id") != latest and r.get("recovery_message_id")}


def synthesis_route_error(route: dict[str, Any], channel: dict[str, Any], message_id: str) -> str:
    """An accepted action must have queued real work for the selected target."""
    if route.get("reply_requests") or route.get("intent_requests") or route.get("queued"):
        return ""
    packs = channel.get("context_packs") or []
    for pack in reversed(list(packs.values()) if isinstance(packs, dict) else packs):
        if pack.get("trigger_message_id") == message_id and pack.get("status") == "rejected":
            return str(pack.get("reason") or "context_pack_rejected")
    return "; ".join(str(row.get("reason") or "not_dispatched") for row in route.get("skipped") or []) or "synthesis_not_dispatched"


def inactive_synthesis_message(channel: dict[str, Any], message: dict[str, Any]) -> bool:
    """Do not resurrect a terminal or superseded formal request through message replay."""
    from zf.runtime.channel_synthesis_lock import synthesis_request_in_flight

    request_id = (message.get("refs") or {}).get("synthesis_request_id")
    if not request_id or not any(r.get("request_id") == request_id
                                 for r in channel.get("synthesis_requests", [])):
        return False
    active = synthesis_request_in_flight(channel, str(message.get("thread_id") or "main"))
    return not active or active.get("request_id") != request_id
