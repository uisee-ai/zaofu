"""Deterministic completion primitives for Agent Channel replies."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_dispatch_claim import ChannelDispatchResult
from zf.runtime.channel_run_owner import provider_run_fields_for_request, reply_generation_is_current
from zf.runtime.channel_reply_remediation import emit_channel_reply_failed
from zf.runtime.channel_sidecar import channel_message_event_payload


def origin_external_refs(message: dict[str, Any]) -> dict[str, Any]:
    """Carry external origins onto replies for bridge routing."""

    refs = message.get("refs") if isinstance(message, dict) else None
    if not isinstance(refs, dict):
        return {}
    return {
        namespace: refs[namespace]
        for namespace in ("feishu", "openclaw")
        if isinstance(refs.get(namespace), dict)
    }


def complete_deterministic_reply(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    message: dict[str, Any],
    request: dict[str, Any],
    request_id: str,
    started_event_id: str,
    actor: str,
    source: str,
    reply: str,
    reason: str,
) -> ChannelDispatchResult:
    """Complete a provider-free reply while preserving the normal lifecycle."""

    channel_id = str(channel.get("channel_id") or request.get("channel_id") or "")
    thread_id = str(request.get("thread_id") or "main")
    run_fields = provider_run_fields_for_request(channel_id, request)
    reply_payload = channel_message_event_payload(state_dir, {
        "channel_id": channel_id,
        "thread_id": thread_id,
        "message_id": f"msg-{request_id}-reply",
        "member_id": str(request.get("target_member_id") or ""),
        "role": "assistant",
        "source": source,
        "text": str(reply).strip() or "（无可展示的状态）",
        "mentions": [],
        "refs": {
            "request_id": request_id,
            **run_fields,
            "deterministic_reply": {
                "kind": "canonical_projection",
                "reason": reason or "deterministic canonical reply completed",
            },
            **origin_external_refs(message),
        },
    }, created_by=f"channel-adapter:{source}", source_event_id=started_event_id)
    message_event = writer.emit(
        "channel.message.posted",
        actor=str(request.get("target_member_id") or actor),
        task_id=str(request.get("task_id") or "") or None,
        causation_id=started_event_id,
        correlation_id=channel_id,
        payload=redact_obj(reply_payload),
    )
    completed_event = writer.emit(
        "channel.agent.reply.completed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=message_event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "reason": reason or "deterministic canonical reply completed",
            **run_fields,
            "source": source,
        },
    )
    from zf.runtime.channel_reply_contract import emit_structured_reply_events

    emit_structured_reply_events(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        request=request,
        message=message,
        reply=reply,
        reply_event_id=completed_event.id,
        actor=actor,
        source=source,
    )
    return ChannelDispatchResult(dispatched=[request_id], completed=[request_id])


def close_superseded_reply(
    *, writer: EventWriter, request: dict[str, Any], request_id: str,
    channel_id: str, started_event_id: str, actor: str, source: str,
    provider_session_id: str,
) -> bool:
    """Close a late attempt before it can publish a reply body or contract."""
    events = writer.event_log.read_all()
    if reply_generation_is_current(events, channel_id=channel_id, request=request):
        from zf.runtime.channel_projection import project_channel
        from zf.runtime.channel_run_owner import RUN_TERMINAL_STATUSES

        channel = project_channel(writer.event_log.path.parent, channel_id, events=events) or {}
        # Supervisor/cancel can settle this very generation while the provider
        # is still returning. Its late answer must not reopen the request.
        return any(
            item.get("request_id") == request_id and item.get("status") in RUN_TERMINAL_STATUSES
            for item in channel.get("reply_requests", [])
        )
    emit_channel_reply_failed(
        writer=writer, request=request, request_id=request_id,
        started_event_id=started_event_id, actor=actor, source=source,
        channel_id=channel_id, reason="superseded by newer requested generation",
        provider_session_id=provider_session_id, failure_status="stale",
    )
    return True
