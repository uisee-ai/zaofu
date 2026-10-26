"""Stable identity and source context for an operator's headless Kanban turn."""

from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.web.plan_requirement_sources import requirement_source_messages


def kanban_turn_context(
    writer: EventWriter, user_message: ZfEvent, payload: dict[str, Any], *,
    requested_action: str, thread_key: str, turn_id: str, run_thread_id: str,
    project_id: str, conversation_id: str, runtime_snapshot_ref: str,
) -> dict[str, Any]:
    return {
        "trace_id": str(payload.get("trace_id") or user_message.correlation_id or ""),
        "pdd_id": str(payload.get("pdd_id") or ""),
        "fanout_id": str(payload.get("fanout_id") or ""),
        "requested_action": requested_action,
        "thread_key": thread_key,
        "turn_id": turn_id,
        "run_thread_id": run_thread_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "operation_kind": "channel_turn" if payload.get("channel_id") else "kanban_turn",
        "runtime_snapshot_ref": runtime_snapshot_ref,
        # Plan Preview repair messages are ordinary operator chat, but the
        # provider still needs the immutable plan identity and bound artifact
        # refs to revise the right candidate. Keep this context read-only;
        # approval and replan remain controlled kernel actions.
        "plan_context": payload.get("plan_context") or {},
        "intent_type": str(payload.get("intent_type") or ""),
        "plan_discussion": payload.get("plan_discussion") or {},
        "requirement_source_messages": requirement_source_messages(
            writer.event_log.read_all(), originating_message_event_id=user_message.id,
            project_id=project_id, conversation_id=conversation_id, thread_key=thread_key or "main",
        )[-20:],
    }
