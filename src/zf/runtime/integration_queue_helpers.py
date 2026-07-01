"""Helper functions for the derived integration queue projection."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.fanout_identity import build_fanout_identity_projection


def payload_dict(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def task_id_for_event(event: ZfEvent, payload: dict[str, Any]) -> str:
    return str(event.task_id or payload.get("task_id") or "")


def queue_reason(payload: dict[str, Any]) -> str:
    for key in ("reason", "error", "message", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def event_issue(event: ZfEvent, entry_id: str, reason: str) -> dict[str, str]:
    return {
        "event_id": event.id,
        "event_type": event.type,
        "entry_id": entry_id,
        "reason": reason,
    }


def stale_reason(
    payload: dict[str, Any],
    fanout_status: dict[str, dict[str, str | bool]],
) -> str:
    if _is_stale(payload):
        return "stale fanout instance"
    fanout_id = str(payload.get("fanout_instance_id") or "")
    if not fanout_id:
        return ""
    status = fanout_status.get(fanout_id)
    if not status:
        return ""
    if status.get("current") is False:
        return str(status.get("stale_reason") or "fanout_instance_not_current")
    return ""


def superseded_by(
    payload: dict[str, Any],
    fanout_status: dict[str, dict[str, str | bool]],
) -> str:
    fanout_id = str(payload.get("fanout_instance_id") or "")
    status = fanout_status.get(fanout_id) or {}
    return str(status.get("superseded_by") or "")


def fanout_status_by_id(events: list[ZfEvent]) -> dict[str, dict[str, str | bool]]:
    try:
        projection = build_fanout_identity_projection(events)
    except Exception:
        return {}
    statuses: dict[str, dict[str, str | bool]] = {}
    for item in projection.get("instances", []) or []:
        if not isinstance(item, dict):
            continue
        fanout_id = str(item.get("fanout_id") or item.get("fanout_instance_id") or "")
        if not fanout_id:
            continue
        statuses[fanout_id] = {
            "current": bool(item.get("current")),
            "stale_reason": str(item.get("stale_reason") or ""),
            "superseded_by": str(item.get("superseded_by") or ""),
        }
    return statuses


def _is_stale(payload: dict[str, Any]) -> bool:
    if payload.get("stale") is True or payload.get("is_stale") is True:
        return True
    if payload.get("stale_fanout_instance") is True:
        return True
    if "current" in payload and payload.get("current") is False:
        return True
    return False
