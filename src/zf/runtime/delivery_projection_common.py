"""Shared helpers for Delivery cockpit projections."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent

EventSlice = Sequence[tuple[int, ZfEvent]]

DONE_STATUSES = {"done", "passed", "approved", "completed", "ready", "shipped", "skipped"}
FAILED_STATUSES = {"failed", "rejected", "error"}
RUNNING_STATUSES = {"in_progress", "running", "dispatched", "dispatching", "aggregating", "rerunning"}
BLOCKED_STATUSES = {"blocked"}


def payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def base_stage_id(stage_id: str) -> str:
    return stage_id[:-10] if stage_id.endswith(":aggregate") else stage_id


def status_kind(status: str) -> str:
    raw = str(status or "").lower()
    if raw in DONE_STATUSES or raw.endswith((".done", ".passed", ".completed", ".approved")):
        return "done"
    if raw in FAILED_STATUSES or raw.endswith((".failed", ".rejected")):
        return "failed"
    if raw in BLOCKED_STATUSES:
        return "blocked"
    if raw in RUNNING_STATUSES or raw.endswith((".started", ".running", ".dispatched", ".requested")):
        return "running"
    return "pending"


def normalize_status(status: str) -> str:
    kind = status_kind(status)
    if kind == "done":
        return "passed" if status in {"passed", "approved", "completed", "ready"} else "done"
    return kind


def event_status(event: ZfEvent) -> str:
    return normalize_status(str(payload(event).get("status") or event.type.rsplit(".", 1)[-1]))


def event_error(event: ZfEvent) -> dict[str, str]:
    data = payload(event)
    if event_status(event) != "failed":
        return {}
    return {
        "type": str(data.get("error_type") or data.get("kind") or event.type),
        "message": str(data.get("message") or data.get("reason") or data.get("error") or ""),
    }


def evidence_refs(data: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("evidence_refs", "artifact_refs", "artifacts"):
        raw = data.get(key)
        if isinstance(raw, list):
            refs.extend(str(item) for item in raw if str(item))
        elif isinstance(raw, str) and raw:
            refs.append(raw)
    return refs


def tools_count(data: dict[str, Any]) -> int:
    raw = data.get("tool_calls")
    if isinstance(raw, list):
        return len(raw)
    return 1 if data.get("tool") or data.get("tool_name") else 0


def dedupe(values: Sequence[str] | Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "")
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def human_label(stage_id: str) -> str:
    return stage_id.replace("_", " ").replace("-", " ").title()
