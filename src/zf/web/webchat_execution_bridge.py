"""Execution bridge between durable WebChat state and headless providers."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.provider_permissions import normalize_kanban_permission_profile
from zf.runtime.channel_prd_context import workflow_context_for_project
from zf.runtime.channel_workflow_authority import bind_task_channel_authority
from zf.runtime.runtime_snapshot import (
    RuntimeSnapshotInput,
    build_runtime_snapshot,
    runtime_snapshot_event_payload,
    write_runtime_snapshot,
)
from zf.web.agent_session_runtime import begin_agent_session_run, run_key
from zf.web.agent_turn_store import AgentTurnConflict, AgentTurnNotFound, AgentTurnStore
from zf.web.headless_agent import HeadlessThreadStore
from zf.web.webchat_runtime import (
    durable_webchat_enabled,
    prepare_durable_webchat_run,
)


ChatHandler = Callable[..., dict[str, Any]]
TurnRunner = Callable[..., dict[str, Any]]
TaskIdResolver = Callable[[dict[str, Any]], str | None]

_HEADLESS_STREAM_FLUSH_INTERVAL_S = 0.15
_KANBAN_AGENT_SIDECAR_THRESHOLD_BYTES = 4 * 1024


def headless_stream_flush_interval_s() -> float:
    raw = str(
        os.environ.get("ZF_KANBAN_AGENT_STREAM_FLUSH_INTERVAL_S")
        or os.environ.get("ZF_KANBAN_AGENT_HEADLESS_STREAM_FLUSH_INTERVAL_S")
        or _HEADLESS_STREAM_FLUSH_INTERVAL_S
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        return _HEADLESS_STREAM_FLUSH_INTERVAL_S
    return min(max(value, 0.05), 2.0)


def kanban_agent_sidecar_threshold_bytes() -> int:
    raw = str(
        os.environ.get("ZF_KANBAN_AGENT_SIDECAR_THRESHOLD_BYTES")
        or _KANBAN_AGENT_SIDECAR_THRESHOLD_BYTES
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        return _KANBAN_AGENT_SIDECAR_THRESHOLD_BYTES
    return max(1, value)


def _headless_chat_sync(payload: dict[str, Any]) -> bool:
    if payload.get("sync") is True:
        return True
    return os.environ.get("ZF_KANBAN_AGENT_HEADLESS_SYNC", "").strip() in {"1", "true", "yes"}


def _headless_thinking_level(payload: dict[str, Any]) -> str:
    return str(
        payload.get("thinking_level")
        or os.environ.get("ZF_KANBAN_AGENT_HEADLESS_THINKING_LEVEL", "")
    ).strip()


def resume_durable_webchat_turn(
    context: Any,
    turn: dict[str, Any],
    interaction: dict[str, Any],
    *,
    chat_handler: ChatHandler,
) -> dict[str, Any]:
    """Resume a waiting WebChat turn as a new fenced provider attempt."""

    request_snapshot = turn.get("request") if isinstance(turn.get("request"), dict) else {}
    original = request_snapshot.get("payload") if isinstance(request_snapshot.get("payload"), dict) else {}
    response = interaction.get("response") if isinstance(interaction.get("response"), dict) else {}
    interaction_kind = str(interaction.get("kind") or "ask_user")
    answer = str(
        response.get("answer")
        or response.get("option_id")
        or response.get("decision")
        or json.dumps(response, ensure_ascii=False, sort_keys=True)
    ).strip()
    payload = {
        **original,
        "message": (
            f"Continue the interrupted request with this operator steer: {answer}"
            if interaction_kind == "control_interrupt"
            else (
                "Continue the same request using this owner answer to the pending "
                f"question: {answer}"
            )
        ),
        "backend": str(turn.get("backend") or original.get("backend") or ""),
        "permission_profile": normalize_kanban_permission_profile(turn.get("permission_profile") or original.get("permission_profile")),
        "project_id": str(turn.get("project_id") or ""),
        "conversation_id": str(turn.get("conversation_id") or ""),
        "thread_key": str(turn.get("thread_id") or "main"),
        "turn_id": str(turn.get("turn_id") or ""),
        "interaction_answer": {
            "kind": interaction_kind,
            "interaction_id": str(interaction.get("interaction_id") or ""),
            "revision": int(interaction.get("revision") or 0),
            "response_digest": str(interaction.get("response_digest") or ""),
        },
        "sync": False,
    }
    plan_response = response.get("plan_response")
    if isinstance(plan_response, dict):
        payload["plan_response"] = dict(plan_response)
    resume_writer = EventWriter(event_log_from_project(
        context.state_dir,
        config=context.config,
    ))
    requested = resume_writer.emit(
        "web.action.requested",
        actor="web",
        task_id=str(turn.get("task_id") or "") or None,
        payload={
            "action": "chat-orchestrator",
            "requested_action": "webchat-interaction-resume",
            "turn_id": str(turn.get("turn_id") or ""),
            "interaction_id": str(interaction.get("interaction_id") or ""),
            "interaction_revision": int(interaction.get("revision") or 0),
        },
    )
    return chat_handler(
        context.state_dir,
        resume_writer,
        requested=requested,
        action="chat-orchestrator",
        requested_action="webchat-interaction-resume",
        payload=payload,
        project_root=context.project_root,
        config=context.config,
    )


def schedule_durable_webchat_recovery(
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig | None,
    chat_handler: ChatHandler,
) -> int:
    """Restart lost-owner turns once; claim fencing prevents duplicate providers."""

    if not durable_webchat_enabled():
        return 0
    store = AgentTurnStore(state_dir)
    candidates = [
        item for item in store.list_turns(states=("resume_queued",), limit=500)
        if str(item.get("terminal_reason") or "") == "lost_owner_lease_expired"
    ]
    scheduled = 0
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    for turn in candidates:
        turn_id = str(turn["turn_id"])
        request_snapshot = turn.get("request") if isinstance(turn.get("request"), dict) else {}
        original = (
            request_snapshot.get("payload")
            if isinstance(request_snapshot.get("payload"), dict)
            else {}
        )
        message = str(request_snapshot.get("message") or original.get("message") or "").strip()
        if not message:
            failed = store.fail_recovery(turn_id, reason="recovery_request_snapshot_missing")
            writer.emit(
                "agent.session.turn.recovery_failed",
                actor="web",
                task_id=str(turn.get("task_id") or "") or None,
                payload={"turn_id": turn_id, "reason": failed["terminal_reason"]},
            )
            continue
        payload = {
            **original,
            "message": message,
            "backend": str(turn.get("backend") or original.get("backend") or ""),
            "permission_profile": normalize_kanban_permission_profile(turn.get("permission_profile") or original.get("permission_profile")),
            "project_id": str(turn.get("project_id") or ""),
            "conversation_id": str(turn.get("conversation_id") or ""),
            "thread_key": str(turn.get("thread_id") or "main"),
            "turn_id": turn_id,
            "webchat_idempotency_key": str(turn.get("idempotency_key") or turn_id),
            "webchat_input_message_id": str(turn.get("input_message_id") or ""),
            "recovery_from_attempt_id": str(turn.get("current_attempt_id") or ""),
            "sync": False,
        }
        requested = writer.emit(
            "web.action.requested",
            actor="web",
            task_id=str(turn.get("task_id") or "") or None,
            payload={
                "action": "chat-orchestrator",
                "requested_action": "webchat-startup-recovery",
                "turn_id": turn_id,
            },
        )
        result = chat_handler(
            state_dir,
            writer,
            requested=requested,
            action="chat-orchestrator",
            requested_action="webchat-startup-recovery",
            payload=payload,
            project_root=project_root,
            config=config,
        )
        if result.get("ok"):
            scheduled += 1
            continue
        try:
            store.fail_recovery(
                turn_id,
                reason=(
                    "recovery_start_rejected:"
                    + str(result.get("reason") or result.get("status") or "unknown")
                ),
            )
        except (AgentTurnConflict, AgentTurnNotFound):
            pass
        writer.emit(
            "agent.session.turn.recovery_failed",
            actor="web",
            task_id=str(turn.get("task_id") or "") or None,
            payload={
                "turn_id": turn_id,
                "reason": str(result.get("reason") or result.get("status") or "unknown"),
            },
        )
    return scheduled


def safe_dispatch_next_durable_webchat_queue(
    *,
    state_dir: Path,
    writer: EventWriter,
    durable_run: Any | None,
    backend: str,
    permission_profile: str,
    project_root: Path,
    config: ZfConfig | None,
    chat_handler: ChatHandler,
) -> dict[str, Any] | None:
    if durable_run is None:
        return None
    if isinstance(durable_run, dict):
        project_id = str(durable_run.get("project_id") or "")
        conversation_id = str(durable_run.get("conversation_id") or "")
        thread_id = str(durable_run.get("thread_id") or "")
        source_turn_id = str(durable_run.get("turn_id") or "")
    else:
        project_id = str(getattr(durable_run, "project_id", ""))
        conversation_id = str(getattr(durable_run, "conversation_id", ""))
        thread_id = str(getattr(durable_run, "thread_id", ""))
        source_turn_id = str(getattr(durable_run, "turn_id", ""))
    if not all((project_id, conversation_id, thread_id)):
        return None
    try:
        item = AgentTurnStore(state_dir).claim_next_queue(
            project_id=project_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            claimed_turn_id=str(uuid.uuid4()),
        )
        if item is None:
            return None
        queue_id = str(item["queue_id"])
        request_payload = item.get("request") if isinstance(item.get("request"), dict) else {}
        payload = {
            **request_payload,
            "message": str(item.get("message") or ""),
            "backend": str(request_payload.get("backend") or backend),
            "permission_profile": normalize_kanban_permission_profile(
                request_payload.get("permission_profile") or permission_profile
            ),
            "project_id": project_id,
            "conversation_id": conversation_id,
            "thread_key": thread_id,
            "turn_id": str(item.get("claimed_turn_id") or ""),
            "webchat_idempotency_key": f"queue:{queue_id}",
            "source_queue_id": queue_id,
            "sync": False,
        }
        requested = writer.emit(
            "web.action.requested",
            actor="web",
            task_id=str(request_payload.get("task_id") or "") or None,
            payload={
                "action": "chat-orchestrator",
                "requested_action": "webchat-queue-dispatch",
                "queue_id": queue_id,
                "source_turn_id": source_turn_id,
                "turn_id": payload["turn_id"],
            },
        )
        try:
            result = chat_handler(
                state_dir,
                writer,
                requested=requested,
                action="chat-orchestrator",
                requested_action="webchat-queue-dispatch",
                payload=payload,
                project_root=project_root,
                config=config,
            )
        except Exception:
            AgentTurnStore(state_dir).finish_queue(queue_id, state="failed")
            raise
        status_code = int(result.get("_status_code") or 200)
        AgentTurnStore(state_dir).finish_queue(
            queue_id,
            state="completed" if status_code < 400 else "failed",
        )
        return {"queue_id": queue_id, "result": result}
    except Exception as exc:
        writer.emit(
            "agent.session.queue.recorded",
            actor="web",
            payload={
                "status": "dispatch_failed",
                "source_turn_id": source_turn_id,
                "reason": str(exc),
            },
        )
        return {"status": "dispatch_failed", "reason": str(exc)}


def handle_headless_kanban_agent_chat(
    state_dir: Path,
    writer: EventWriter,
    *,
    requested: ZfEvent,
    user_message: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict[str, Any],
    message: str,
    backend: str,
    project_root: Path,
    config: ZfConfig | None,
    task_id_from_payload: TaskIdResolver,
    run_turn: TurnRunner,
) -> dict[str, Any]:
    task_id = task_id_from_payload(payload)
    model = str(payload.get("model") or "").strip()
    if model and (backend != "codex-headless" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model)):
        return {"_status_code": 400, "ok": False, "status": "invalid_model", "reason": "model selection is not supported"}
    workflow_context = workflow_context_for_project(payload, project_root)
    thread_key = str(payload.get("thread_key") or "").strip()
    permission_profile = normalize_kanban_permission_profile(payload.get("permission_profile"))
    turn_id = str(payload.get("turn_id") or uuid.uuid4())
    project_id = str(payload.get("project_id") or "")
    conversation_id = str(payload.get("conversation_id") or "")
    run_thread_id = thread_key or str(payload.get("thread_id") or "").strip() or "main"

    durable_run = None
    if durable_webchat_enabled():
        try:
            durable_run = prepare_durable_webchat_run(
                state_dir=state_dir,
                project_root=project_root,
                payload=payload,
                message=message,
                backend=backend,
                permission_profile=permission_profile,
                turn_id=turn_id,
                user_message_id=str(payload.get("webchat_input_message_id") or user_message.id),
                task_id=task_id or "",
            )
        except AgentTurnConflict as exc:
            return {
                "_status_code": 409,
                "ok": False,
                "status": "turn_conflict",
                "action": action,
                "requested_action": requested_action,
                "reason": str(exc),
                "turn_id": turn_id,
            }
        project_id = durable_run.project_id
        conversation_id = durable_run.conversation_id
        thread_key = durable_run.thread_id
        run_thread_id = durable_run.thread_id
    else:
        project_id = project_id or project_root.name or "default"
        conversation_id = conversation_id or f"kanban-agent:{project_id}"
        thread_key = run_thread_id
    process_run_id = (
        str(durable_run.claim.attempt_id) if durable_run is not None else turn_id
    )
    begin_agent_session_run(
        run_key(
            run_id=process_run_id,
            thread_id=run_thread_id,
            project_id=project_id,
            conversation_id=conversation_id,
        ),
        provider=backend,
    )
    turn_created = writer.emit(
        "kanban.agent.turn.created",
        actor="web",
        task_id=task_id,
        causation_id=user_message.id,
        correlation_id=user_message.correlation_id,
        payload={
            "turn_id": turn_id,
            "thread_key": thread_key,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "backend": backend,
            "requested_model": model,
            "requested_thinking_level": _headless_thinking_level(payload),
            "permission_profile": permission_profile,
            "scope": str(payload.get("scope") or "project"),
            "message_event_id": user_message.id,
            "requested_action": requested_action,
        },
    )
    turn_started = writer.emit(
        "kanban.agent.turn.started",
        actor="web",
        task_id=task_id,
        causation_id=turn_created.id,
        correlation_id=user_message.correlation_id,
        payload={
            "turn_id": turn_id,
            "thread_key": thread_key,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "backend": backend,
            "permission_profile": permission_profile,
            "message_event_id": user_message.id,
        },
    )
    runner_kwargs = {
        "state_dir": state_dir,
        "writer": writer,
        "user_message": user_message,
        "turn_started": turn_started,
        "action": action,
        "requested_action": requested_action,
        "payload": dict(payload),
        "message": message,
        "backend": backend,
        "project_root": project_root,
        "workflow_context": workflow_context,
        "task_id": task_id,
        "thread_key": thread_key,
        "turn_id": turn_id,
        "run_thread_id": run_thread_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "thinking_level": _headless_thinking_level(payload),
        **({"model": model} if model else {}),
        "permission_profile": permission_profile,
        "config": config,
        "durable_run": durable_run,
        "process_run_id": process_run_id,
    }
    if _headless_chat_sync(payload):
        return run_turn(**runner_kwargs)

    def run_background() -> None:
        bg_writer = EventWriter(event_log_from_project(state_dir, config=config))
        run_turn(**{**runner_kwargs, "writer": bg_writer})

    threading.Thread(
        target=run_background,
        name=f"zf-kanban-agent-{turn_id[:8]}",
        daemon=True,
    ).start()
    return {
        "_status_code": 202,
        "ok": True,
        "status": "accepted",
        "action": action,
        "requested_action": requested_action,
        "reason": "headless kanban agent turn accepted; stream events carry progress",
        "event_id": user_message.id,
        "turn_event_id": turn_started.id,
        "turn_id": turn_id,
        "thread_key": thread_key,
        "trace_id": user_message.correlation_id,
        "backend": backend,
        "permission_profile": permission_profile,
    }


@dataclass(frozen=True)
class HeadlessRuntimeContext:
    task: Any | None
    workflow_context: dict[str, Any]
    snapshot_ref: str


def prepare_headless_runtime_context(
    *,
    state_dir: Path,
    project_root: Path,
    project_id: str,
    payload: dict[str, Any],
    backend: str,
    task_id: str | None,
    thread_key: str,
    turn_id: str,
    workflow_context: dict[str, Any],
    writer: EventWriter,
    turn_started: ZfEvent,
    user_message: ZfEvent,
) -> HeadlessRuntimeContext:
    """Bind task authority and record the provider runtime snapshot."""

    task = None
    snapshot_ref = ""
    try:
        from types import SimpleNamespace

        task = TaskStore(state_dir / "kanban.json").get(task_id) if task_id else None
        if task is not None:
            workflow_context = bind_task_channel_authority(workflow_context, task)
        role = SimpleNamespace(
            name="kanban-agent",
            instance_id="kanban-agent",
            role_kind="operator",
            backend=backend,
            publishes=[],
        )
        thread_store = HeadlessThreadStore(state_dir=state_dir, project_root=project_root)
        stored_thread_id = thread_store.thread_id(
            scope=str(payload.get("scope") or "project"),
            task_id=task_id or "",
            thread_key=thread_key,
        )
        snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
            state_dir=state_dir,
            project_root=project_root,
            project_id=project_id,
            source="headless_run",
            task=task,
            role=role,
            dispatch_id=turn_id,
            run_id=turn_id,
            trace_id=user_message.correlation_id or "",
            refs={"headless_thread_ref": thread_store.path_for(stored_thread_id)},
            output_contract={
                "expected_event": "kanban.agent.reply",
                "verification_tiers": [],
                "evidence_contract": {},
            },
        ))
        snapshot_result = write_runtime_snapshot(
            snapshot,
            state_dir=state_dir,
            project_root=project_root,
        )
        snapshot_ref = snapshot_result.snapshot_ref
        writer.append(ZfEvent(
            type="runtime.snapshot.recorded",
            actor="web",
            task_id=task_id,
            payload=runtime_snapshot_event_payload(snapshot_result),
            causation_id=turn_started.id,
            correlation_id=user_message.correlation_id,
        ))
    except Exception as snapshot_exc:
        try:
            writer.append(ZfEvent(
                type="runtime.snapshot.invalid",
                actor="web",
                task_id=task_id,
                payload={
                    "source": "headless_run",
                    "reason": str(snapshot_exc),
                    "run_id": turn_id,
                },
                causation_id=turn_started.id,
                correlation_id=user_message.correlation_id,
            ))
        except Exception:
            pass
    return HeadlessRuntimeContext(task, workflow_context, snapshot_ref)


__all__ = [
    "HeadlessRuntimeContext",
    "handle_headless_kanban_agent_chat",
    "headless_stream_flush_interval_s",
    "kanban_agent_sidecar_threshold_bytes",
    "prepare_headless_runtime_context",
    "resume_durable_webchat_turn",
    "safe_dispatch_next_durable_webchat_queue",
    "schedule_durable_webchat_recovery",
]
