"""Durable pending-proposal projection (chat-e2e F2).

``kanban.agent.action.proposed`` is ledger truth, but the approval card used
to exist only in the originating browser session's DOM — closing the browser
orphaned the proposal. This fold gives every surface (panel, inbox, API) the
same session-independent pending list. Resolution comes from an explicit
``kanban.agent.proposal.resolved`` event (execute / dismiss), a
``task.created`` whose request threads ``proposal_event_id``, or — as a
fallback for out-of-band executions — a ``task.created`` with the same title.
"""
from __future__ import annotations

from typing import Any, Iterable

from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj

PROPOSAL_RESOLVED_EVENT = "kanban.agent.proposal.resolved"


def pending_kanban_proposals(events: Iterable[ZfEvent]) -> list[dict[str, Any]]:
    pending: dict[str, dict[str, Any]] = {}
    resolved: set[str] = set()
    created_titles: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "kanban.agent.action.proposed":
            proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
            action_payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
            pending[event.id] = {
                "proposal_event_id": event.id,
                "ts": event.ts,
                "action": str(proposal.get("action") or ""),
                "requested_action": str(proposal.get("requested_action") or ""),
                "reason": str(proposal.get("reason") or ""),
                "valid": bool(proposal.get("valid")),
                "validation_error": str(proposal.get("validation_error") or ""),
                "title": str(action_payload.get("title") or ""),
                "payload": action_payload,
                "turn_id": str(payload.get("turn_id") or ""),
                "conversation_id": str(payload.get("conversation_id") or ""),
                "thread_key": str(payload.get("thread_key") or ""),
            }
        elif event.type == PROPOSAL_RESOLVED_EVENT:
            resolved.add(str(payload.get("proposal_event_id") or ""))
        elif event.type == "task.created":
            request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
            threaded = str(request.get("proposal_event_id") or "")
            if threaded:
                resolved.add(threaded)
            task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            title = str(request.get("title") or task.get("title") or "").strip()
            if title:
                created_titles.add(title)
    out = []
    for event_id, record in pending.items():
        if event_id in resolved:
            continue
        if (
            record["action"] in {"create-task", "idea-to-product"}
            and record["title"]
            and record["title"].strip() in created_titles
        ):
            continue
        out.append(redact_obj(record))
    out.reverse()  # newest first
    return out
