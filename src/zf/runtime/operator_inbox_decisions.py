"""Decision item helpers for the read-only operator inbox projection."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from zf.runtime.channel_question_graph import OWNER_QUESTION_KINDS


def is_owner_question(payload: dict[str, Any]) -> bool:
    kind = str(payload.get("kind") or "owner_decision").strip().lower()
    target = str(payload.get("target_member_id") or "owner").strip().lower()
    return (
        kind in OWNER_QUESTION_KINDS
        and target in {"owner", "operator", "owner:operator"}
    )


def bind_channel_question_aliases(
    aliases: dict[str, str],
    payload: dict[str, Any],
    key: str,
) -> None:
    for alias in _channel_question_aliases(payload):
        aliases[alias] = key


def channel_question_key(
    aliases: dict[str, str],
    payload: dict[str, Any],
) -> str:
    for alias in _channel_question_aliases(payload):
        if alias in aliases:
            return aliases[alias]
    return ""


def channel_decision_item(
    *,
    event: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    question_id = str(payload.get("question_id") or _event_id(event)).strip()
    channel_id = str(payload.get("channel_id") or "").strip()
    thread_id = str(payload.get("thread_id") or "main").strip() or "main"
    deep_link = "?page=channels"
    if channel_id:
        deep_link += f"&channel={quote(channel_id, safe='')}"
    deep_link += f"&thread={quote(thread_id, safe='')}&question={quote(question_id, safe='')}"
    return {
        "id": f"channel-question:{channel_id or 'unknown'}:{thread_id}:{question_id}",
        "kind": "channel_decision",
        "status": "pending",
        "title": str(payload.get("header") or "Channel decision"),
        "summary": str(payload.get("question") or "Owner input requested"),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "source_role": "channel",
        "source_actor": _actor(event),
        "question_id": question_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "asked_by": str(payload.get("asked_by") or _actor(event)),
        "question_kind": str(payload.get("kind") or "owner_decision"),
        "decision_options": payload.get("options") or [],
        "allow_other": payload.get("allow_other", True),
        "recommended_answer": str(payload.get("recommended_answer") or ""),
        "priority": str(payload.get("priority") or "p1"),
        "deep_link": deep_link,
        "deep_link_label": "Open channel",
        "actions": [],
    }


def kanban_plan_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("plan_request") or payload.get("request") or {}
    return request if isinstance(request, dict) else {}


def kanban_question_item_id(request_id: str, revision: int) -> str:
    return f"kanban-question:{request_id}:r{revision}"


def kanban_question_item(
    *,
    event: Any,
    payload: dict[str, Any],
    request: dict[str, Any],
    request_id: str,
    revision: int,
) -> dict[str, Any]:
    questions = request.get("questions")
    question_count = len(questions) if isinstance(questions, list) else 1
    question = str(request.get("question") or "").strip()
    if not question and isinstance(questions, list) and questions:
        first = questions[0] if isinstance(questions[0], dict) else {}
        question = str(first.get("question") or "").strip()
    summary = (
        f"{question_count} decisions requested"
        if question_count > 1
        else question or "Kanban Agent needs input"
    )
    thread_id = str(request.get("thread_key") or payload.get("thread_key") or "main")
    deep_link = (
        "?page=project&agent=open"
        f"&agent_thread={quote(thread_id, safe='')}"
        f"&agent_request={quote(_event_id(event), safe='')}"
    )
    return {
        "id": kanban_question_item_id(request_id, revision),
        "kind": "kanban_question",
        "status": "pending",
        "title": str(request.get("header") or "Kanban Agent question"),
        "summary": summary,
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "source_role": "kanban_agent",
        "source_actor": _actor(event),
        "request_event_id": _event_id(event),
        "request_id": request_id,
        "revision": revision,
        "subject_type": str(request.get("subject_type") or "clarification"),
        "conversation_id": str(
            request.get("conversation_id")
            or payload.get("conversation_id")
            or ""
        ),
        "thread_id": thread_id,
        "question_count": question_count,
        "decision_options": request.get("options") or [],
        "decision_questions": questions if isinstance(questions, list) else [],
        "deep_link": deep_link,
        "deep_link_label": "Open Kanban Agent",
        "actions": [],
    }


def _channel_question_aliases(payload: dict[str, Any]) -> list[str]:
    question_id = str(payload.get("question_id") or "").strip()
    channel_id = str(payload.get("channel_id") or "").strip()
    if not question_id:
        return []
    if channel_id:
        return [f"question:{channel_id}:{question_id}"]
    return [f"question:{question_id}"]


def _event_id(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("id") or "")
    return str(getattr(event, "id", "") or "")


def _event_ts(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("ts") or "")
    return str(getattr(event, "ts", "") or "")


def _actor(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("actor") or "")
    return str(getattr(event, "actor", "") or "")
