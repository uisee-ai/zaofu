"""Bind agent-selected requirement refs to earlier messages in the same scope."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events import ZfEvent
from zf.runtime.kanban_plan_requests import plan_requirement_digest


def requirement_source_messages(
    events: Iterable[ZfEvent], *, originating_message_event_id: str,
    project_id: str, conversation_id: str, thread_key: str,
) -> list[dict[str, str]]:
    rows = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "user.message" and all(
            not expected or str(payload.get(key) or "") == expected
            for key, expected in (
                ("project_id", project_id), ("conversation_id", conversation_id),
                ("thread_key", thread_key),
            )
        ):
            message = str(payload.get("message") or payload.get("text") or "").strip()
            if message:
                rows.append({"event_id": event.id, "message": message})
        if event.id == originating_message_event_id:
            return rows
    return []


def bind_requirement_sources(raw: dict[str, Any], context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    selected = raw.get("originating_message_event_ids")
    if selected is not None and (
        not isinstance(selected, list) or not selected
        or any(not isinstance(ref, str) or not ref.strip() for ref in selected)
    ):
        return context, "originating_message_event_ids must be a nonempty list of message refs"
    catalog = context.get("requirement_source_messages")
    if not isinstance(catalog, list):
        return context, "explicit requirement refs need ledger context" if selected is not None else ""
    origin = str(context.get("originating_message_event_id") or "")
    current = str(context.get("requirement_source_cutoff_event_id") or origin)
    # A continuation has a stable origin and a new operator input. Bind both;
    # source selection may not silently discard either side of that intent.
    refs = set(selected or [origin]) | {origin, current}
    available = {row["event_id"] for row in catalog}
    if refs - available:
        return context, "requirement refs must identify existing earlier messages in this project, conversation and thread"
    rows = [(row["event_id"], row["message"]) for row in catalog if row["event_id"] in refs]
    return {
        **context,
        "originating_message_event_ids": [ref for ref, _ in rows],
        "requirement_digest": plan_requirement_digest(rows),
        "user_semantic_context": "\n\n".join(message for _, message in rows),
    }, ""
