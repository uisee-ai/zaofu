"""Owner identity for durable Channel input events."""
from __future__ import annotations

from zf.core.events import ZfEvent


def is_owner_message(event: ZfEvent, *, channel_id: str, thread_id: str, owner: str) -> bool:
    payload = event.payload or {}
    if (not owner or event.type != "channel.message.posted" or event.correlation_id != channel_id
            or str(payload.get("thread_id") or "main") != thread_id or payload.get("role") != "user"):
        return False
    if event.actor == owner:
        return True
    # The controlled web ingress writes actor=web for the operator identity.
    return (owner == "owner:operator" and event.actor == "web" and payload.get("source") == "web"
            and payload.get("member_id") in {"operator", "owner:operator"})
