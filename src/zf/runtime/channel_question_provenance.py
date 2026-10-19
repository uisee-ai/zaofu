"""Bind question navigation to runtime reply identity, including legacy replay."""
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_run_owner import stable_provider_run_id


QUESTION_SOURCE_FIELDS = frozenset({"source_run_id", "source_message_id"})
QUESTION_SOURCE_EVENT_TYPES = frozenset({
    "channel.agent.reply.completed", "channel.synthesis.proposed", "channel.consensus.blocked",
})


def question_source_fields(payload: dict[str, Any]) -> dict[str, str]:
    return {key: payload[key].strip() for key in QUESTION_SOURCE_FIELDS
            if isinstance(payload.get(key), str) and payload[key].strip()}


def question_source_for_request(channel_id: str, request: dict[str, Any]) -> dict[str, str]:
    run_id = str(request.get("run_id") or request.get("provider_run_id") or "")
    if not run_id and request.get("request_id") and request.get("target_member_id"):
        run_id = stable_provider_run_id(
            channel_id=channel_id, thread_id=str(request.get("thread_id") or "main"),
            request_id=str(request["request_id"]), target_member_id=str(request["target_member_id"]),
        )
    return {
        "source_run_id": run_id,
        # The UI locates a turn by its triggering input, not the assistant body ID.
        "source_message_id": str(request.get("message_id") or ""),
    }


def recover_question_source(event: ZfEvent, prior_events: dict[str, ZfEvent]) -> dict[str, str]:
    explicit = question_source_fields(event.payload)
    if explicit:
        return explicit
    channel_id = str(event.payload.get("channel_id") or "")
    thread_id = str(event.payload.get("thread_id") or "main")
    member_id = str(event.payload.get("asked_by") or event.actor)
    cause = event.causation_id
    seen = {event.id}
    while cause and cause not in seen:
        seen.add(cause)
        parent = prior_events.get(cause)
        if parent is None or parent.type not in QUESTION_SOURCE_EVENT_TYPES:
            break
        payload = parent.payload
        if (payload.get("channel_id") != channel_id
                or str(payload.get("thread_id") or "main") != thread_id):
            break
        if parent.type == "channel.agent.reply.completed":
            if payload.get("target_member_id") == member_id:
                return question_source_fields(question_source_for_request(channel_id, payload))
            break
        cause = parent.causation_id
    return {}
