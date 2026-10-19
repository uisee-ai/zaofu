"""Persist incomplete public output as evidence, never as successful work."""
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.runtime.channel_run_owner import provider_run_fields_for_request
from zf.runtime.channel_sidecar import channel_message_event_payload


def persist_partial_reply(
    *, state_dir: Path, writer: EventWriter, result: Any, request: dict[str, Any],
    channel_id: str, request_id: str, actor: str, started_event_id: str, backend: str,
) -> None:
    text = str(getattr(result, "reply", "") or "")
    if not text:
        return
    writer.emit(
        "channel.message.posted", actor=str(request.get("target_member_id") or actor),
        causation_id=started_event_id, correlation_id=channel_id,
        payload=channel_message_event_payload(state_dir, {
            "channel_id": channel_id, "thread_id": request.get("thread_id") or "main",
            "message_id": f"msg-{request_id}-partial-{request.get('run_generation') or 1}",
            "member_id": request.get("target_member_id"), "role": "assistant",
            "source": backend, "text": text, "mentions": [],
            "public_output_items": [item.to_dict() for item in getattr(result, "messages", [])
                                    if hasattr(item, "to_dict") and item.type in {"text", "status"}],
            "refs": {"request_id": request_id, "incomplete": True,
                     "provider_session_id": str(getattr(result, "provider_session_id", "") or ""),
                     **provider_run_fields_for_request(channel_id, request)},
        }, created_by="channel-adapter:partial", source_event_id=started_event_id),
    )
