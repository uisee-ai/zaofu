"""Bounded mechanical retry for incomplete Channel question dedup plans."""

from __future__ import annotations

from typing import Any

from zf.core.events import EventWriter
from zf.runtime.channel_question_dedup import (
    MAX_QUESTION_DEDUP_ATTEMPTS,
    question_ledger,
    question_ledger_digest,
    stable_question_dedup_request_id,
)


def repair_rejected_question_dedup(
    writer: EventWriter,
    channel: dict[str, Any],
    session: dict[str, Any],
    *,
    actor: str,
    source: str,
    channel_id: str,
    thread_id: str,
) -> list[str]:
    requests = [
        item
        for item in channel.get("question_dedup_requests") or []
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
        and (not session.get("started_at") or not (item.get("updated_at") or item.get("ts"))
             or str(item.get("updated_at") or item.get("ts")) >= str(session["started_at"]))
    ]
    if (not requests and session.get("phase_reason") == "blind_round_complete"
            and session.get("synthesizer")):
        ledger = question_ledger(channel, thread_id=thread_id)
        if any(item["status"] == "open" for item in ledger):
            writer.emit(
                "channel.question.dedup.requested", actor=actor, correlation_id=channel_id,
                payload={
                    "schema_version": "channel.question.dedup.v1", "channel_id": channel_id,
                    "thread_id": thread_id, "request_id": stable_question_dedup_request_id(
                        channel_id, thread_id, str(session.get("started_event_id") or "")),
                    "target_member_id": str(session["synthesizer"]),
                    "ledger_digest": question_ledger_digest(channel, thread_id=thread_id),
                    "question_count": len(ledger), "generation": 1, "source": source,
                },
            )
            return ["channel.question.dedup.requested"]
    if not requests or any(
        str(item.get("status") or "") == "applied"
        for item in requests
    ):
        return []
    latest = requests[-1]
    latest_status = str(latest.get("status") or "")
    failed_reply: dict[str, Any] = {}
    if latest_status == "requested":
        failed_reply = _failed_reply_for_dedup_request(
            channel,
            str(latest.get("request_id") or ""),
        )
        if not failed_reply:
            return []
    elif latest_status != "rejected":
        return []
    generation = max(
        int(item.get("generation") or index)
        for index, item in enumerate(requests, 1)
    )
    prior_request_id = str(latest.get("request_id") or "")
    repair_reason = (
        "dedup_reply_failed"
        if failed_reply
        else str(latest.get("reason") or "dedup_rejected")
    )
    causation_id = str(
        failed_reply.get("event_id")
        or latest.get("result_event_id")
        or ""
    ) or None
    if generation >= MAX_QUESTION_DEDUP_ATTEMPTS:
        writer.emit(
            "channel.question.dedup.remediation.exhausted",
            actor=actor,
            causation_id=causation_id,
            correlation_id=channel_id,
            payload={
                "schema_version": "channel.question.dedup.v1",
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": prior_request_id,
                "attempts": generation,
                "reason": repair_reason,
                "source": source,
            },
        )
        return ["channel.question.dedup.remediation.exhausted"]

    next_generation = generation + 1
    request_id = stable_question_dedup_request_id(
        channel_id,
        thread_id,
        str(session.get("started_event_id") or ""),
        generation=next_generation,
    )
    if any(
        str(item.get("request_id") or "") == request_id
        for item in requests
    ):
        return []
    ledger = question_ledger(channel, thread_id=thread_id)
    writer.emit(
        "channel.question.dedup.requested",
        actor=actor,
        causation_id=causation_id,
        correlation_id=channel_id,
        payload={
            "schema_version": "channel.question.dedup.v1",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "target_member_id": str(session.get("synthesizer") or ""),
            "ledger_digest": question_ledger_digest(
                channel,
                thread_id=thread_id,
            ),
            "question_count": len(ledger),
            "generation": next_generation,
            "prior_request_id": prior_request_id,
            "repair_reason": repair_reason,
            "source": source,
        },
    )
    return ["channel.question.dedup.requested"]


def _failed_reply_for_dedup_request(
    channel: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    if not request_id:
        return {}
    message_ids = {
        str(message.get("message_id") or "")
        for message in channel.get("messages") or []
        if isinstance(message, dict)
        and str(
            (message.get("refs") or {}).get("question_dedup_request_id")
            if isinstance(message.get("refs"), dict)
            else ""
        ) == request_id
    }
    if not message_ids:
        return {}
    failures = [
        reply
        for reply in channel.get("reply_requests") or []
        if isinstance(reply, dict)
        and str(reply.get("message_id") or "") in message_ids
        and str(reply.get("status") or "") == "failed"
    ]
    return failures[-1] if failures else {}


__all__ = ["repair_rejected_question_dedup"]
