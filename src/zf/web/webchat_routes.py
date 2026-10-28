"""Thin HTTP adapters for the durable Kanban Agent WebChat runtime."""

from __future__ import annotations

import base64
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.web.agent_artifact_store import (
    AgentArtifactAccessDenied,
    AgentArtifactConflict,
    AgentArtifactNotFound,
    AgentArtifactStore,
    validate_adoption_target,
)
from zf.web.agent_child_activity import build_child_activity_projection
from zf.web.agent_conversation_store import (
    AgentConversationStore,
    ConversationConflict,
    ConversationNotFound,
)
from zf.web.agent_profile_builder import (
    AgentProfileBuilder,
    AgentProfileConflict,
    AgentProfileNotFound,
)
from zf.web.agent_runtime_db import SCHEMA_VERSION
from zf.web.agent_session_runtime import cancel_agent_session_run, run_key
from zf.web.agent_tool_policy import AgentToolCatalog
from zf.web.agent_turn_store import AgentTurnConflict, AgentTurnNotFound, AgentTurnStore
from zf.web.agent_turn_contract import (
    TURN_ACTIVE_STATES,
    TURN_CLAIMABLE_STATES,
    TURN_TERMINAL_STATES,
)
from zf.web.agent_webchat_contract import build_legacy_shadow_projection, capabilities_for_surface
from zf.web.projections.request_util import _request_json
from zf.web.webchat_runtime import durable_webchat_enabled

_MODEL_CATALOG_LOCK = threading.BoundedSemaphore(1)


def build_webchat_router(
    *,
    resolve_ctx: Callable[[str], Any],
    mutation_auth_error: Callable[..., dict | None],
    session_cookie: Callable[[Request], str | None],
    resume_turn: Callable[[Any, dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    dispatch_queue: Callable[[Any, dict[str, Any]], dict[str, Any] | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def auth(
        action: str,
        request: Request,
        authorization: str | None,
        token: str | None,
    ) -> JSONResponse | None:
        error = mutation_auth_error(
            action,
            authorization=authorization,
            x_zf_web_token=token,
            web_session_token=session_cookie(request),
        )
        if error is None:
            return None
        body = dict(error)
        return JSONResponse(body, status_code=int(body.pop("_status_code", 403)))

    def services(project_id: str):
        ctx = resolve_ctx(project_id)
        return (
            ctx,
            AgentTurnStore(ctx.state_dir),
            AgentConversationStore(ctx.state_dir),
            AgentArtifactStore(ctx.state_dir, project_root=ctx.project_root),
        )

    def writer(ctx: Any) -> EventWriter:
        return EventWriter(event_log_from_project(ctx.state_dir, config=ctx.config))

    def emit(ctx: Any, event_type: str, payload: Mapping[str, Any], *, task_id: str = "") -> None:
        writer(ctx).emit(
            event_type,
            actor="web",
            task_id=task_id or None,
            payload=redact_obj(dict(payload)),
        )

    @router.get("/api/projects/{project_id}/webchat/runtime")
    def runtime(
        project_id: str,
        conversation_id: str = "",
        thread_id: str = "",
        surface: str = "kanban_agent",
        include_shadow: bool = False,
    ) -> JSONResponse:
        ctx, turns, conversations, artifacts = services(project_id)
        current_profile = AgentProfileBuilder(
            ctx.state_dir, project_root=ctx.project_root,
        ).current("kanban-agent")
        payload = {
            "schema_version": "webchat-runtime.v1",
            "rollout": {
                "mode": "durable" if durable_webchat_enabled() else "legacy",
                "store_schema": SCHEMA_VERSION,
                "rollback_env": "ZF_KANBAN_WEBCHAT_RUNTIME=legacy",
            },
            "project_id": project_id,
            "conversation_id": conversation_id,
            "thread_id": thread_id,
            "capabilities": capabilities_for_surface(surface, native_steer=False),
            "turns": turns.list_turns(
                project_id=project_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                limit=160,
            ),
            "interactions": turns.list_interactions(
                project_id=project_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                limit=160,
            ),
            "queue": turns.list_queue(
                project_id=project_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                include_terminal=True,
                terminal_limit=4,
            ),
            "control_receipts": turns.list_control_receipts(
                project_id=project_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
            ),
            "threads": conversations.list_threads(
                project_id=project_id,
                conversation_id=conversation_id,
            ) if conversation_id else [],
            "messages": conversations.list_messages(
                project_id=project_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                limit=500,
            )["items"] if conversation_id and thread_id else [],
            "artifacts": artifacts.list_versions(
                project_id=project_id,
                conversation_id=conversation_id,
                principal="kanban-agent",
            ),
            "tool_catalog": AgentToolCatalog().projection(surface=surface),
            "agent_profile": {
                "profile_id": (current_profile or {}).get("profile_id") or "",
                "revision": (current_profile or {}).get("revision") or 0,
                "profile_digest": (current_profile or {}).get("profile_digest") or "",
                "profile": (current_profile or {}).get("profile") or {},
            },
        }
        if include_shadow:
            payload["shadow"] = build_legacy_shadow_projection(
                writer(ctx).event_log.read_all(),
            )
        return JSONResponse(payload)

    @router.get("/api/projects/{project_id}/webchat/model-catalog")
    def model_catalog(
        project_id: str, request: Request, thread_key: str = "", task_id: str = "",
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("chat-orchestrator", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        from zf.web.kanban_model_catalog import codex_model_catalog

        ctx = resolve_ctx(project_id)
        if not _MODEL_CATALOG_LOCK.acquire(timeout=20):
            raise HTTPException(503, "model catalog is busy; retry")
        try:
            return JSONResponse(codex_model_catalog(ctx.project_root, state_dir=ctx.state_dir, thread_key=thread_key, task_id=task_id))
        except Exception as exc:
            raise HTTPException(503, "model catalog unavailable") from exc
        finally:
            _MODEL_CATALOG_LOCK.release()

    @router.get("/api/projects/{project_id}/webchat/turns/{turn_id}/parts")
    def turn_parts(project_id: str, turn_id: str, after_seq: int = 0, limit: int = 500) -> JSONResponse:
        _, turns, _, _ = services(project_id)
        try:
            turn = turns.get(turn_id)
            if str(turn["project_id"]) != project_id:
                raise AgentTurnNotFound(turn_id)
            return JSONResponse(turns.replay_parts(turn_id, after_seq=after_seq, limit=limit))
        except AgentTurnNotFound:
            raise HTTPException(404, "turn not found") from None

    @router.post("/api/projects/{project_id}/webchat/interactions/{interaction_id}/answer")
    async def answer_interaction(
        project_id: str,
        interaction_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-interaction-answer", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, turns, _, _ = services(project_id)
        payload = await _request_json(request)
        try:
            pending = turns.get_interaction(interaction_id)
            turn = turns.get(str(pending["turn_id"]))
            if str(turn["project_id"]) != project_id:
                raise AgentTurnNotFound(interaction_id)
            requested_revision = int(payload.get("revision") or 0)
            if int(pending["revision"]) != requested_revision:
                raise AgentTurnConflict(
                    f"stale interaction revision {requested_revision}; "
                    f"current is {int(pending['revision'])}"
                )
            item, consumed = turns.answer_interaction(
                interaction_id,
                revision=requested_revision,
                response=dict(payload.get("response") or {}),
                expected_currentness=(
                    dict(payload["currentness"])
                    if isinstance(payload.get("currentness"), Mapping)
                    else None
                ),
                resume=payload.get("resume") is not False,
            )
        except AgentTurnNotFound:
            raise HTTPException(404, "interaction not found") from None
        except (AgentTurnConflict, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        emit(ctx, "agent.session.interaction.answered", {
            "interaction_id": interaction_id,
            "revision": item["revision"],
            "turn_id": item["turn_id"],
            "response_digest": item["response_digest"],
            "consumed": consumed,
            "resume_requested": payload.get("resume") is not False,
        })
        resumed = None
        if (
            consumed
            and payload.get("resume") is not False
            and resume_turn is not None
            and item.get("kind") == "ask_user"
        ):
            resumed = resume_turn(ctx, turns.get(str(item["turn_id"])), item)
        elif consumed and payload.get("resume") is False:
            resumed = {"status": "resolved_without_resume"}
        elif consumed:
            resumed = {"status": "awaiting_exact_interaction_consumer"}
        return JSONResponse({
            "ok": True,
            "consumed": consumed,
            "interaction": item,
            "resume": resumed,
        })

    @router.post("/api/projects/{project_id}/webchat/queue")
    async def enqueue(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-queue", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, turns, _, _ = services(project_id)
        payload = await _request_json(request)
        try:
            thread_id = str(payload.get("thread_id") or payload.get("thread_key") or "")
            item, created = turns.enqueue(
                project_id=project_id,
                conversation_id=str(payload.get("conversation_id") or ""),
                thread_id=thread_id,
                message=str(payload.get("message") or ""),
                idempotency_key=str(payload.get("idempotency_key") or ""),
                request=dict(payload),
                source_turn_id=str(payload.get("source_turn_id") or ""),
            )
        except (ValueError, AgentTurnConflict) as exc:
            raise HTTPException(400, str(exc)) from exc
        emit(ctx, "agent.session.queue.recorded", {
            "queue_id": item["queue_id"], "created": created,
            "source_turn_id": item["source_turn_id"],
        })
        queue_dispatch = None
        source_turn_id = str(item.get("source_turn_id") or "")
        if source_turn_id and dispatch_queue is not None:
            try:
                source_turn = turns.get(source_turn_id)
            except AgentTurnNotFound:
                source_turn = None
            if (
                source_turn is not None
                and str(source_turn.get("project_id") or "") == project_id
                and str(source_turn.get("conversation_id") or "")
                == str(item.get("conversation_id") or "")
                and str(source_turn.get("thread_id") or "")
                == str(item.get("thread_id") or "")
                and str(source_turn.get("state") or "") in TURN_TERMINAL_STATES
            ):
                blocking = turns.list_turns(
                    project_id=project_id,
                    conversation_id=str(item.get("conversation_id") or ""),
                    thread_id=str(item.get("thread_id") or ""),
                    states=tuple(TURN_ACTIVE_STATES | TURN_CLAIMABLE_STATES),
                    limit=1,
                )
                if not blocking:
                    queue_dispatch = dispatch_queue(ctx, source_turn)
        return JSONResponse({
            "ok": True,
            "created": created,
            "item": item,
            "queue_dispatch": queue_dispatch,
        }, status_code=201 if created else 200)

    @router.delete("/api/projects/{project_id}/webchat/queue/{queue_id}")
    def cancel_queue(
        project_id: str,
        queue_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-queue-cancel", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        _, turns, _, _ = services(project_id)
        try:
            return JSONResponse({
                "ok": True,
                "item": turns.cancel_queue(queue_id, project_id=project_id),
            })
        except AgentTurnNotFound:
            raise HTTPException(404, "queue item not found") from None
        except AgentTurnConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/projects/{project_id}/webchat/turns/{turn_id}/control")
    async def control_turn(
        project_id: str,
        turn_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-turn-control", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, turns, _, _ = services(project_id)
        payload = await _request_json(request)
        operation = str(payload.get("operation") or "")
        try:
            turn = turns.get(turn_id)
            if str(turn["project_id"]) != project_id:
                raise AgentTurnNotFound(turn_id)
            receipt, created = turns.apply_control(
                operation=operation,
                turn_id=turn_id,
                idempotency_key=str(payload.get("idempotency_key") or ""),
                native_steer=False,
                message=str(payload.get("message") or ""),
            )
        except AgentTurnNotFound:
            raise HTTPException(404, "turn not found") from None
        except (AgentTurnConflict, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        process = None
        if created and operation in {"interrupt", "cancel"}:
            turn = turns.get(turn_id)
            process = cancel_agent_session_run(run_key(
                run_id=str(turn.get("current_attempt_id") or turn_id),
                thread_id=str(turn["thread_id"]),
                project_id=project_id,
                conversation_id=str(turn["conversation_id"]),
            ))
        resumed = None
        if created and operation == "interrupt" and resume_turn is not None:
            resumed = resume_turn(ctx, turns.get(turn_id), {
                "kind": "control_interrupt",
                "interaction_id": receipt["receipt_id"],
                "revision": 1,
                "response_digest": "",
                "response": {
                    "answer": str(payload.get("message") or "Continue after operator interrupt"),
                },
            })
        emit(ctx, "agent.session.control.recorded", {
            "receipt_id": receipt["receipt_id"], "turn_id": turn_id,
            "operation": operation, "status": receipt["status"],
            "fallback": receipt["fallback"],
        })
        return JSONResponse({
            "ok": True, "created": created, "receipt": receipt,
            "process": vars(process) if process is not None else None,
            "resume": resumed,
        })

    @router.get("/api/projects/{project_id}/webchat/conversations/{conversation_id}/threads")
    def threads(project_id: str, conversation_id: str) -> JSONResponse:
        _, _, conversations, _ = services(project_id)
        return JSONResponse({"items": conversations.list_threads(project_id=project_id, conversation_id=conversation_id)})

    @router.get("/api/projects/{project_id}/webchat/conversations/{conversation_id}/threads/{thread_id}/messages")
    def messages(project_id: str, conversation_id: str, thread_id: str, before: str = "", limit: int = 100) -> JSONResponse:
        _, _, conversations, _ = services(project_id)
        return JSONResponse(conversations.list_messages(
            project_id=project_id, conversation_id=conversation_id,
            thread_id=thread_id, before=before, limit=limit,
        ))

    @router.get("/api/projects/{project_id}/webchat/conversations/search")
    def search_messages(project_id: str, q: str, conversation_id: str = "", thread_id: str = "") -> JSONResponse:
        _, _, conversations, _ = services(project_id)
        return JSONResponse({"items": conversations.search(
            project_id=project_id, query=q, conversation_id=conversation_id, thread_id=thread_id,
        )})

    @router.post("/api/projects/{project_id}/webchat/messages/{message_id}/correct")
    async def correct_message(
        project_id: str, message_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-message-correct", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        _, _, conversations, _ = services(project_id)
        payload = await _request_json(request)
        try:
            return JSONResponse({"ok": True, "message": conversations.correct(
                message_id, content=str(payload.get("content") or ""), project_id=project_id,
            )})
        except ConversationNotFound:
            raise HTTPException(404, "message not found") from None
        except ConversationConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/projects/{project_id}/webchat/messages/{message_id}/fork")
    async def fork_message(
        project_id: str, message_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-message-fork", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        _, _, conversations, _ = services(project_id)
        payload = await _request_json(request)
        try:
            return JSONResponse({"ok": True, "thread": conversations.fork(
                message_id, title=str(payload.get("title") or "Fork"), project_id=project_id,
            )})
        except ConversationNotFound:
            raise HTTPException(404, "message not found") from None

    @router.post("/api/projects/{project_id}/webchat/conversations/{conversation_id}/threads/{thread_id}/compact")
    async def compact_thread(
        project_id: str, conversation_id: str, thread_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-thread-compact", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        _, _, conversations, _ = services(project_id)
        payload = await _request_json(request)
        try:
            item = conversations.compact(
                project_id=project_id, conversation_id=conversation_id, thread_id=thread_id,
                summary=str(payload.get("summary") or ""),
                covered_message_ids=[str(item) for item in payload.get("covered_message_ids") or []],
            )
        except (ValueError, ConversationConflict, ConversationNotFound) as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse({"ok": True, "summary": item})

    @router.get("/api/projects/{project_id}/webchat/conversations/{conversation_id}/threads/{thread_id}/export")
    def export_thread(project_id: str, conversation_id: str, thread_id: str, format: str = "markdown") -> Response:
        _, _, conversations, _ = services(project_id)
        try:
            media_type, body = conversations.export(
                project_id=project_id, conversation_id=conversation_id, thread_id=thread_id, format=format,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="conversation.{format}"'})

    @router.post("/api/projects/{project_id}/webchat/artifacts")
    async def upload_artifact(
        project_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-artifact-upload", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, _, _, artifacts = services(project_id)
        payload = await _request_json(request)
        try:
            data = base64.b64decode(str(payload.get("data_base64") or ""), validate=True)
            item, created = artifacts.capture_bytes(
                project_id=project_id,
                logical_name=str(payload.get("logical_name") or "attachment"),
                data=data,
                media_type=str(payload.get("media_type") or ""),
                producer={"agent_id": "kanban-agent", "origin": "web-upload"},
                lineage=dict(payload.get("lineage") or {}),
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        emit(ctx, "agent.artifact.captured", {
            "version_id": item["version_id"], "artifact_id": item["artifact_id"],
            "digest": item["digest"], "storage_uri": item["storage_uri"], "created": created,
        })
        return JSONResponse({"ok": True, "created": created, "artifact": item}, status_code=201 if created else 200)

    @router.get("/api/projects/{project_id}/webchat/artifacts/{version_id}")
    def artifact_detail(project_id: str, version_id: str) -> JSONResponse:
        _, _, _, artifacts = services(project_id)
        try:
            item, body = artifacts.read(
                version_id, principal="kanban-agent", project_id=project_id,
                limit=256 * 1024, redact=True,
            )
        except AgentArtifactNotFound:
            raise HTTPException(404, "artifact not found") from None
        except AgentArtifactAccessDenied:
            raise HTTPException(403, "artifact access denied") from None
        preview = body.decode("utf-8", errors="replace") if str(item["media_type"]).startswith("text/") else ""
        return JSONResponse({"artifact": item, "preview": preview, "preview_truncated": len(body) < int(item["size_bytes"])})

    @router.get("/api/projects/{project_id}/webchat/artifacts/{version_id}/content")
    def artifact_content(project_id: str, version_id: str) -> Response:
        _, _, _, artifacts = services(project_id)
        try:
            item, body = artifacts.read(
                version_id, principal="kanban-agent", project_id=project_id, redact=True,
            )
        except AgentArtifactNotFound:
            raise HTTPException(404, "artifact not found") from None
        except AgentArtifactAccessDenied:
            raise HTTPException(403, "artifact access denied") from None
        return Response(body, media_type=str(item["media_type"]), headers={
            "Content-Disposition": f'attachment; filename="{Path(str(item["logical_name"])).name}"',
            "X-Content-Type-Options": "nosniff",
        })

    @router.get("/api/projects/{project_id}/webchat/artifacts/compare/{left}/{right}")
    def compare_artifacts(project_id: str, left: str, right: str) -> JSONResponse:
        _, _, _, artifacts = services(project_id)
        try:
            return JSONResponse({
                "diff": artifacts.compare(
                    left, right, principal="kanban-agent", project_id=project_id,
                )
            })
        except (AgentArtifactNotFound, AgentArtifactAccessDenied):
            raise HTTPException(404, "artifact version not available") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/api/projects/{project_id}/webchat/artifacts/{version_id}/publish")
    async def publish_artifact(
        project_id: str, version_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-artifact-publish", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, _, _, artifacts = services(project_id)
        payload = await _request_json(request)
        try:
            item = artifacts.publish(
                version_id, expected_digest=str(payload.get("digest") or ""),
                principal="kanban-agent", project_id=project_id,
            )
        except AgentArtifactNotFound:
            raise HTTPException(404, "artifact not found") from None
        except AgentArtifactAccessDenied:
            raise HTTPException(403, "artifact access denied") from None
        except AgentArtifactConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        emit(ctx, "agent.artifact.published", {
            "version_id": version_id, "artifact_id": item["artifact_id"], "digest": item["digest"],
        })
        return JSONResponse({"ok": True, "artifact": item})

    @router.post("/api/projects/{project_id}/webchat/artifacts/{version_id}/adopt")
    async def adopt_artifact(
        project_id: str, version_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-artifact-adopt", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, _, _, artifacts = services(project_id)
        payload = await _request_json(request)
        try:
            target = validate_adoption_target(payload.get("target") or {})
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        task_id = str(target.get("task_id") or "")
        if task_id:
            task = TaskStore(ctx.state_dir / "kanban.json").get(task_id)
            if task is None:
                raise HTTPException(404, "target task not found")
            expected_revision = target.get(
                "contract_authority_revision", target.get("revision"),
            )
            current_revision = str(task.contract_authority_revision or "")
            if not str(expected_revision or ""):
                raise HTTPException(409, "target task contract authority revision is required")
            if str(expected_revision) != current_revision:
                raise HTTPException(409, "target task contract authority revision changed")
        try:
            item = artifacts.mark_adopted(
                version_id, expected_digest=str(payload.get("digest") or ""),
                target=target, principal="kanban-agent", project_id=project_id,
            )
        except AgentArtifactNotFound:
            raise HTTPException(404, "artifact not found") from None
        except AgentArtifactAccessDenied:
            raise HTTPException(403, "artifact access denied") from None
        except (AgentArtifactConflict, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        emit(ctx, "agent.artifact.adopted", {
            "version_id": version_id, "artifact_id": item["artifact_id"],
            "digest": item["digest"], "target": target,
        }, task_id=task_id)
        return JSONResponse({"ok": True, "artifact": item, "controlled": True})

    @router.get("/api/projects/{project_id}/webchat/adoption-targets/tasks/{task_id}")
    def task_adoption_target(project_id: str, task_id: str) -> JSONResponse:
        ctx, _, _, _ = services(project_id)
        task = TaskStore(ctx.state_dir / "kanban.json").get(task_id)
        if task is None:
            raise HTTPException(404, "target task not found")
        revision = str(task.contract_authority_revision or "")
        if not revision:
            raise HTTPException(409, "target task has no contract authority revision")
        return JSONResponse({
            "schema_version": "webchat-artifact-adoption-target.v1",
            "kind": "task_input",
            "task_id": task_id,
            "contract_authority_revision": revision,
        })

    @router.get("/api/projects/{project_id}/webchat/child-activities")
    def child_activities(project_id: str, parent_turn_id: str = "") -> JSONResponse:
        ctx, _, _, _ = services(project_id)
        events = writer(ctx).event_log.read_all()
        statuses = {task.id: task.status for task in TaskStore(ctx.state_dir / "kanban.json").list_all()}
        return JSONResponse(build_child_activity_projection(
            events, parent_turn_id=parent_turn_id, task_status=statuses,
        ))

    @router.post("/api/projects/{project_id}/webchat/tools/evaluate")
    async def evaluate_tool(
        project_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-tool-evaluate", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, _, _, _ = services(project_id)
        payload = await _request_json(request)
        try:
            turn = AgentTurnStore(ctx.state_dir).get(str(payload.get("turn_id") or ""))
            if str(turn["project_id"]) != project_id:
                raise AgentTurnNotFound(str(payload.get("turn_id") or ""))
        except AgentTurnNotFound:
            raise HTTPException(404, "turn not found") from None
        decision = AgentToolCatalog().evaluate(
            state_dir=ctx.state_dir,
            turn_id=str(payload.get("turn_id") or ""),
            attempt_id=str(payload.get("attempt_id") or ""),
            name=str(payload.get("tool") or ""),
            args=dict(payload.get("args") or {}),
            workspace=str(ctx.project_root),
            surface="kanban_agent",
            unattended=False,
            overrides={},
        )
        return JSONResponse(decision.as_dict())

    @router.get("/api/projects/{project_id}/webchat/profiles/catalog")
    def profile_catalog(project_id: str) -> JSONResponse:
        ctx, _, _, _ = services(project_id)
        builder = AgentProfileBuilder(ctx.state_dir, project_root=ctx.project_root)
        result = builder.catalog()
        current = builder.current("kanban-agent")
        result["current_profile"] = (current or {}).get("profile") or {}
        result["current_revision"] = (current or {}).get("revision") or 0
        result["current_digest"] = (current or {}).get("profile_digest") or ""
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/webchat/profiles/{profile_id}/proposals")
    async def propose_profile(
        project_id: str, profile_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-profile-propose", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, _, _, _ = services(project_id)
        payload = await _request_json(request)
        try:
            result, created = AgentProfileBuilder(
                ctx.state_dir, project_root=ctx.project_root,
            ).propose(
                project_id=project_id, profile_id=profile_id,
                base_profile=dict(payload.get("base_profile") or {}),
                proposed_profile=dict(payload.get("profile") or {}),
                permission_ceiling=str(payload.get("permission_ceiling") or "workspace_writer"),
            )
        except AgentProfileConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        emit(ctx, "agent.profile.proposed", {
            "proposal_id": result["proposal_id"], "profile_id": profile_id,
            "proposal_digest": result["proposal_digest"], "created": created,
        })
        return JSONResponse({"ok": True, "created": created, "proposal": result})

    @router.post("/api/projects/{project_id}/webchat/profiles/proposals/{proposal_id}/apply")
    async def apply_profile(
        project_id: str, proposal_id: str, request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth("webchat-profile-apply", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx, _, _, _ = services(project_id)
        payload = await _request_json(request)
        try:
            revision = AgentProfileBuilder(ctx.state_dir, project_root=ctx.project_root).apply(
                proposal_id,
                expected_base_digest=str(payload.get("base_digest") or ""),
                expected_proposal_digest=str(payload.get("proposal_digest") or ""),
            )
        except AgentProfileNotFound:
            raise HTTPException(404, "profile proposal not found") from None
        except AgentProfileConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        emit(ctx, "agent.profile.applied", {
            "proposal_id": proposal_id, "profile_id": revision["profile_id"],
            "profile_digest": revision["profile_digest"], "revision": revision["revision"],
        })
        return JSONResponse({"ok": True, "revision": revision})

    return router


__all__ = ["build_webchat_router"]
