"""Durable stream-query lifecycle facts, independent of in-memory notifications."""
from __future__ import annotations

import asyncio
from typing import Any, Callable
from uuid import uuid4

from zf.core.events import EventLog, EventWriter, ZfEvent


def run_stream_turn(state_dir, context, session_id: str, invoke: Callable[[], Any], *, publish_excerpt: bool = False):
    fields = context.to_payload()
    required = ("run_id", "task_id", "operation_id", "attempt_id", "dispatch_id", "lease_id", "instance_id", "task_pipeline_stage")
    if publish_excerpt:
        required = tuple(k for k in required if k not in {"task_pipeline_stage", "task_id"})
    if not all(fields.get(key) for key in required):
        return invoke()  # Legacy/non-task calls retain their existing recovery route.
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    identity = {**fields, "workflow_run_id": fields["run_id"], "backend": "claude-code",
                "session_id": session_id, "turn_id": str(uuid4()), "transport": "stream-json"}
    def emit(kind, payload):
        return writer.append(ZfEvent(type=kind, origin="kernel", actor=fields["instance_id"],
            task_id=fields.get("task_id"), correlation_id=fields["run_id"], payload={**identity, **payload}))
    opened = emit("provider.turn.opened", {})
    try:
        messages, status = invoke()
    except BaseException as exc:
        # No exception body, prompt, native transcript or credentials in the ledger.
        emit("provider.turn.closed", {"provider_status": "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
            "provider_stop_reason": "manual_interrupt" if isinstance(exc, asyncio.CancelledError) else "transport_error",
            "business_completed": False, "recovery_owner": "run_manager",
            "next_action": "reconcile_provider_outcome"})
        raise
    final = [msg for msg in messages if all(hasattr(msg, key) for key in ("subtype", "is_error", "num_turns"))]
    completed = (getattr(status, "value", status) == "ok" and final
                 and not final[-1].is_error and final[-1].subtype == "success"
                 and getattr(final[-1], "session_id", "") in {"", session_id})
    pending, unknown = set(), False
    for message in messages:
        for block in getattr(message, "content", []) or []:
            if hasattr(block, "name") and hasattr(block, "input"):
                tool_id = getattr(block, "id", "")
                if tool_id:
                    pending.add(tool_id)
                else:
                    unknown = True
            if hasattr(block, "tool_use_id"):
                pending.discard(block.tool_use_id)
    excerpt = {}
    if publish_excerpt and completed and len(final) == 1 and session_id and getattr(final[0], "session_id", "") == session_id:
        from zf.runtime.workflow_session_excerpt import capture_public_reply
        body = getattr(final[0], "result", None)
        if isinstance(body, str) and body.strip():
            try:
                excerpt = {"operator_excerpt_ref": capture_public_reply(state_dir, opened, body)}
            except (OSError, ValueError):
                pass  # Observing a reply must never affect its execution or settlement.
    emit("provider.turn.closed", {**excerpt, "provider_status": "completed" if completed else "incomplete",
        "drain_status": getattr(status, "value", str(status)), "pending_tool_ids": sorted(pending),
        "tool_identity_unknown": unknown, "business_completed": False,
        "recovery_owner": "kernel" if completed and not pending and not unknown else "run_manager",
        "next_action": "reconcile_task_result_or_continue" if completed and not pending and not unknown else "reconcile_provider_outcome"})
    return messages, status


def open_stream_turn(events, actor: str, *, require_unique: bool = False) -> ZfEvent | None:
    """An unclosed durable invocation prevents another automatic continuation."""
    opened = {}
    for event in events:
        if event.origin != "kernel" or event.actor != actor or event.payload.get("backend") != "claude-code":
            continue
        key = (event.payload.get("session_id"), event.payload.get("turn_id"))
        if event.type == "provider.turn.opened":
            opened[key] = event
        elif event.type == "provider.turn.closed":
            opened.pop(key, None)
    if require_unique and len(opened) != 1:
        return None
    return next(reversed(opened.values()), None)


def current_stream_turn_matches(events, event, attempt) -> bool:
    if event.origin != "kernel" or event.payload.get("transport") != "stream-json":
        return False
    expected = {key: attempt.get(key) for key in ("operation_id", "attempt_id", "dispatch_id", "lease_id", "instance_id", "task_id")}
    expected["workflow_run_id"] = attempt.get("run_id")
    if any(not value or event.payload.get(key) != value for key, value in expected.items()):
        return False
    opened = [e for e in events if e.type == "provider.turn.opened" and e.origin == "kernel" and e.actor == event.actor]
    if not opened or open_stream_turn(events, event.actor) is not None:
        return False
    source = opened[-1]
    from zf.runtime.provider_turn_liveness import _parse_event_ts

    created, started, closed = (_parse_event_ts(value) for value in (attempt.get("created_at"), source.ts, event.ts))
    if not created or not started or not closed or not created <= started <= closed:
        return False
    return (all(source.payload.get(key) == value for key, value in expected.items())
        and all(source.payload.get(key) == event.payload.get(key) for key in ("session_id", "turn_id", "backend"))
        and bool(event.payload.get("session_id")) and bool(event.payload.get("turn_id")))
