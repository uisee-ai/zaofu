"""Typed next-round contract helpers for Channel synthesis output."""

from __future__ import annotations

from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.runtime.channel_contract_artifacts import normalize_synthesis_next_round


def validate_synthesis_next_round(
    synthesis: dict[str, Any],
    *,
    channel: dict[str, Any],
    thread_id: str,
    question_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Return one valid adaptive-round request without deciding its merit."""

    next_round, error = normalize_synthesis_next_round(
        synthesis.get("next_round")
    )
    if error:
        return {}, error
    has_open_questions = any(
        isinstance(question, dict)
        and str(question.get("thread_id") or "main") == thread_id
        and str(question.get("status") or "") == "open"
        for question in channel.get("open_questions") or []
    )
    if next_round["action"] == "continue" and (
        question_records or has_open_questions
    ):
        return {}, "next_round.continue requires no open owner questions"
    return next_round, ""


def emit_synthesis_next_round_proposal(
    *,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    synthesis_event: ZfEvent,
    synthesis_request_id: str,
    next_round: dict[str, Any],
    actor: str,
    source: str,
    task_id: str,
) -> None:
    """Publish the fact-bound intent consumed by the adaptive-round reactor."""

    if next_round.get("action") != "continue":
        return
    session = (
        channel.get("discussions", {}).get(thread_id, {})
        if isinstance(channel.get("discussions"), dict)
        else {}
    )
    writer.emit(
        "channel.discussion.next_round.proposed",
        actor=actor,
        task_id=task_id or None,
        causation_id=synthesis_event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "synthesis_event_id": synthesis_event.id,
            "synthesis_request_id": synthesis_request_id,
            "discussion_id": str(session.get("discussion_id") or ""),
            "expected_revision": int(session.get("revision") or 0),
            "expected_context_digest": str(
                session.get("context_digest") or ""
            ),
            "reason": str(next_round.get("reason") or ""),
            "objective": str(next_round.get("objective") or ""),
            "target_member_ids": list(
                next_round.get("target_member_ids") or []
            ),
            "source": source,
        },
    )


__all__ = [
    "emit_synthesis_next_round_proposal",
    "validate_synthesis_next_round",
]
