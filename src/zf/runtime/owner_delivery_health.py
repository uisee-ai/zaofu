"""Read-only per-route status of owner notification delivery."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent


def owner_delivery_health(events: Iterable[ZfEvent]) -> list[dict[str, Any]]:
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if event.type not in {"owner.visible_message.failed", "owner.visible_message.delivered"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        target = str(payload.get("target") or "unknown")
        route = str(payload.get("route") or "unknown")
        row = routes.setdefault((target, route), {
            "target": target, "route": route, "failure_count": 0,
        })
        if event.type.endswith(".failed"):
            row["failure_count"] += 1
            row["status"] = "failed"
            row["reason"] = str(payload.get("reason") or payload.get("error_class") or "")
        else:
            row["status"] = "delivered"
            row["reason"] = ""
        row["last_at"] = event.ts
    return sorted(routes.values(), key=lambda row: (row["target"], row["route"]))
