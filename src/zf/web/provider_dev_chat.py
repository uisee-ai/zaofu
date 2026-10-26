"""Provider-backed development chat action handlers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import normalize_permission_profile
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.web.agent_session_runtime import (
    cancel_agent_session_run,
    run_key,
)
from zf.web.headless_agent import canonical_headless_backend


def handle_provider_dev_chat(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    project_root: Path | None,
    project_id: str,
    config: ZfConfig | None,
    chat_handler: Callable[..., dict],
    backend_available: Callable[[str], bool],
) -> dict:
    if action == "provider-dev-chat-stop":
        if payload.get("run_id") or payload.get("turn_id"):
            return handle_agent_session_cancel(
                writer,
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
                project_id=project_id,
            )
        return ControlledActionService(
            state_dir,
            writer,
            config=config,
            project_root=project_root,
            actor="web",
            source="kanban-agent",
            surface="web",
        ).execute(
            action=action,
            requested_action=requested_action,
            payload=payload,
            requested=requested,
        )

    raw_backend = str(
        payload.get("backend") or payload.get("provider") or ""
    ).strip()
    if not raw_backend:
        raw_backend = str(
            os.environ.get("ZF_KANBAN_AGENT_BACKEND", "")
            or getattr(
                getattr(config, "orchestrator", None),
                "backend",
                "",
            )
        )
    backend = canonical_headless_backend(raw_backend)
    if not backend:
        backend = next(
            (
                candidate
                for candidate in (
                    "claude-headless",
                    "codex-headless",
                )
                if backend_available(candidate)
            ),
            "codex-headless",
        )
    request_event = writer.emit(
        {
            "provider-dev-chat-start": (
                "provider.dev_chat.start.requested"
            ),
            "provider-dev-chat-send": (
                "provider.dev_chat.message.requested"
            ),
        }[action],
        actor="web",
        task_id=_task_id_from_payload(payload),
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload=redact_obj({
            "schema_version": "provider.dev_chat.request.v1",
            "provider": backend,
            "thread_id": str(
                payload.get("thread_id")
                or payload.get("thread_key")
                or ""
            ),
            "permission_profile": normalize_permission_profile(
                payload.get("permission_profile")
            ),
            "source": "kanban-agent",
            "surface": "web",
            "request": payload,
        }),
    )
    chat_payload = {
        **payload,
        "backend": backend,
        "message": str(
            payload.get("message") or payload.get("objective") or ""
        ).strip(),
        "thread_key": str(
            payload.get("thread_key")
            or payload.get("thread_id")
            or ""
        ).strip(),
    }
    return chat_handler(
        state_dir,
        writer,
        requested=request_event,
        action=action,
        requested_action=requested_action,
        payload=chat_payload,
        project_root=project_root,
        config=config,
    )


def handle_agent_session_cancel(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    project_id: str,
) -> dict:
    task_id = _task_id_from_payload(payload)
    run_id = str(
        payload.get("run_id") or payload.get("turn_id") or ""
    ).strip()
    thread_id = str(
        payload.get("thread_id") or payload.get("thread_key") or ""
    ).strip()
    # Public WebChat events identify the turn; the process registry identifies
    # its current attempt. Persist cancellation before signalling the process
    # so late provider output cannot settle or resume a cancelled turn.
    process_run_id = run_id
    from zf.web.agent_runtime_db import database_path
    from zf.web.agent_turn_contract import AgentTurnConflict, AgentTurnNotFound, TURN_TERMINAL_STATES
    from zf.web.agent_turn_store import AgentTurnStore

    state_dir = writer.event_log.path.parent
    if database_path(state_dir).exists():
        turns = AgentTurnStore(state_dir)
        try:
            turn = turns.get(run_id)
        except AgentTurnNotFound:
            turn = None
        if turn is not None:
            if turn["thread_id"] != thread_id:
                return {"ok": False, "_status_code": 409, "status": "turn_conflict",
                        "reason": "cancel thread does not match the public turn"}
            process_run_id = str(turn.get("current_attempt_id") or run_id)
            if turn["state"] not in TURN_TERMINAL_STATES:
                try:
                    receipt, _ = turns.apply_control(
                        operation="cancel", turn_id=run_id,
                        idempotency_key=f"agent-session-cancel:{requested.id}",
                    )
                except AgentTurnConflict as exc:
                    return {"ok": False, "_status_code": 409, "status": "turn_conflict", "reason": str(exc)}
                process_run_id = str(receipt.get("attempt_id") or process_run_id)
    cancel_result = cancel_agent_session_run(run_key(
        run_id=process_run_id,
        thread_id=thread_id,
        project_id=project_id,
        conversation_id=str(payload.get("conversation_id") or ""),
    ))
    event = writer.emit(
        "agent.session.run.cancelled",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=str(
            payload.get("conversation_id")
            or project_id
            or requested.correlation_id
        ),
        payload={
            "project_id": project_id,
            "conversation_id": str(
                payload.get("conversation_id") or ""
            ),
            "thread_id": thread_id,
            "run_id": run_id,
            "provider": str(
                payload.get("backend")
                or payload.get("provider")
                or ""
            ),
            "reason": str(
                payload.get("reason")
                or "operator cancelled agent session run"
            ),
            "status": cancel_result.status,
            "interrupt_supported": cancel_result.interrupt_supported,
            "process_found": cancel_result.process_found,
            "process_terminated": cancel_result.process_terminated,
            "pid": cancel_result.pid or "",
            "source": "web",
        },
    )
    completion = {
        "action": action,
        "requested_action": requested_action,
        "status": "cancel_requested",
        "run_id": run_id,
        "thread_id": thread_id,
        "interrupt_status": cancel_result.status,
        "process_terminated": cancel_result.process_terminated,
    }
    for event_type in (
        "runtime.action.completed",
        "web.action.completed",
    ):
        writer.emit(
            event_type,
            actor="web",
            task_id=task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id,
            payload=completion,
        )
    return {
        "ok": True,
        "status": cancel_result.status,
        "action": action,
        "requested_action": requested_action,
        "reason": (
            cancel_result.reason
            or "agent session cancel request recorded; "
            "provider interrupt is best-effort"
        ),
        "event_id": event.id,
        "run_id": run_id,
        "thread_id": thread_id,
        "interrupt_supported": cancel_result.interrupt_supported,
        "process_found": cancel_result.process_found,
        "process_terminated": cancel_result.process_terminated,
        "pid": cancel_result.pid,
    }


__all__ = [
    "handle_agent_session_cancel",
    "handle_provider_dev_chat",
]
