"""Turn typed Channel review blockers into durable revision or owner work."""

from __future__ import annotations

from typing import Any

from zf.core.events import EventWriter, ZfEvent


def discussion_tick_is_self_loop(event: ZfEvent) -> bool:
    if event.actor == "orchestrator-reactor":
        return True
    if event.actor != "channel-discussion":
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    return not (
        event.type == "channel.question.resolved"
        and payload.get("resolution") == "revision_requested"
    )


def automatic_revision_request_count(
    channel: dict[str, Any], *, thread_id: str, started_at: str,
) -> int:
    """Count syntheses after the first runtime-acknowledged review revision."""
    revision_times = [
        str(item.get("ts") or "")
        for item in channel.get("open_questions") or []
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
        and item.get("kind") == "revision_request"
        and item.get("resolution") == "revision_requested"
        and str(item.get("ts") or "") >= started_at
    ]
    if not revision_times:
        return 0
    first_revision_at = min(revision_times)
    return sum(
        str(item.get("thread_id") or "main") == thread_id
        and str(item.get("ts") or "") > first_revision_at
        for item in channel.get("synthesis_requests") or []
        if isinstance(item, dict)
    )


def open_unseen_consensus_blockers(
    writer: EventWriter,
    channel: dict[str, Any],
    consensus: dict[str, Any],
    *,
    actor: str,
    source: str,
    channel_id: str,
    thread_id: str,
    synthesizer: str,
) -> list[str]:
    emitted: list[str] = []
    existing = {
        str(item.get("question_id") or "")
        for item in channel.get("open_questions") or []
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
    }
    for item in consensus.get("blocked") or []:
        question_id = str(item.get("blocker_question_id") or "")
        if not question_id or question_id in existing:
            continue
        revision_request = (
            str(item.get("blocker_kind") or "owner_decision")
            == "revision_request"
        )
        blocker = str(item.get("blocker_question") or "")
        opened = writer.emit(
            "channel.question.opened",
            actor=actor,
            causation_id=str(item.get("event_id") or "") or None,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "question": blocker,
                "category": "blocker",
                "kind": "revision_request" if revision_request else "owner_decision",
                "target_member_id": synthesizer if revision_request else "owner",
                "asked_by": str(item.get("member_id") or ""),
                "source": source,
            },
        )
        emitted.append("channel.question.opened")
        existing.add(question_id)
        if revision_request:
            writer.emit(
                "channel.question.resolved",
                actor="channel-discussion",
                causation_id=opened.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "question_id": question_id,
                    "resolution": "revision_requested",
                    "resolved_by": "channel-discussion",
                    "answer": blocker,
                    "source": source,
                },
            )
            emitted.append("channel.question.resolved")
    return emitted


__all__ = [
    "automatic_revision_request_count",
    "discussion_tick_is_self_loop",
    "open_unseen_consensus_blockers",
]
