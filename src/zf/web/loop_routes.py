"""Loop Web API routes (doc94).

Read-only sibling router for ``loop.v1``. The route reads EventLog once per
request and delegates all projection logic to ``zf.runtime.loop_projection``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.loop_actions import LoopActionRequest, request_loop_action
from zf.runtime.loop_learning_promotion import (
    LoopLearningPromotionRequest,
    request_loop_learning_promotion,
)
from zf.runtime.loop_projection import build_loop_projection
from zf.web.projections.common import _action_payload, _payload_hash
from zf.web.projections.request_util import (
    _complete_idempotency_key,
    _request_json,
    _reserve_idempotency_key,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_loop_router(
    *,
    resolve_ctx: Callable[[str], Any],
    authorize_mutation: Callable[..., dict | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def _projection(project_id: str) -> dict[str, Any]:
        ctx = resolve_ctx(project_id)
        source_seq = 0
        cache_key = f"loop-projection:{project_id}"
        try:
            from zf.web.projections import read_model

            source_seq = read_model.current_projected_seq(ctx.state_dir, config=ctx.config)
            cached = read_model.get_cached_projection(
                ctx.state_dir,
                cache_key,
                source_seq=source_seq,
            )
            if cached is not None:
                return cached
        except Exception:
            source_seq = 0
        events = list(enumerate(event_log_from_project(ctx.state_dir, config=ctx.config).read_all()))
        projection = build_loop_projection(events=events, generated_at=_now(), project_id=project_id)
        if source_seq:
            try:
                from zf.web.projections import read_model

                read_model.set_cached_projection(
                    ctx.state_dir,
                    cache_key,
                    kind="loop-projection",
                    source_seq=source_seq,
                    payload=projection,
                )
            except Exception:
                pass
        return projection

    @router.get("/api/projects/{project_id}/loops")
    def loops(project_id: str) -> JSONResponse:
        return JSONResponse(_projection(project_id))

    @router.post("/api/projects/{project_id}/loops/{loop_id}/actions")
    async def loop_action(
        project_id: str,
        loop_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        if authorize_mutation is None:
            return JSONResponse({
                "ok": False,
                "status": "disabled",
                "reason": "loop action mutation is not configured",
            }, status_code=403)
        auth_error = authorize_mutation(
            "loop-action-request",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=request.cookies.get("zf_web_session"),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)

        raw_payload = await _request_json(request)
        request_payload = _action_payload({**raw_payload, "loop_id": loop_id})
        idempotency_key = str(
            x_idempotency_key
            or raw_payload.get("idempotency_key")
            or raw_payload.get("request_id")
            or "",
        )
        ctx = resolve_ctx(project_id)
        payload_hash = _payload_hash(request_payload)
        if idempotency_key:
            reserved = _reserve_idempotency_key(
                ctx.state_dir,
                key=idempotency_key,
                action="loop-action-request",
                payload_hash=payload_hash,
            )
            status = reserved.get("status")
            if status == "replayed":
                response = dict(reserved.get("response") or {})
                response["idempotency"] = {"key": idempotency_key, "status": "replayed"}
                return JSONResponse(response, status_code=int(response.pop("_status_code", 202)))
            if status == "pending":
                return JSONResponse({
                    "ok": True,
                    "status": "duplicate_pending",
                    "reason": "idempotent loop action request is already pending",
                    "idempotency": {"key": idempotency_key, "status": "pending"},
                }, status_code=202)
            if status == "conflict":
                return JSONResponse({
                    "ok": False,
                    "status": "idempotency_key_conflict",
                    "reason": "idempotency key already used with a different loop action payload",
                    "idempotency": {"key": idempotency_key, "status": "conflict"},
                }, status_code=409)

        event_log = event_log_from_project(ctx.state_dir, config=ctx.config)
        events = list(enumerate(event_log.read_all()))
        projection = build_loop_projection(events=events, generated_at=_now(), project_id=project_id)
        result = request_loop_action(
            events=events,
            projection=projection,
            writer=EventWriter(event_log),
            request=LoopActionRequest(
                loop_id=loop_id,
                candidate_id=str(raw_payload.get("candidate_id") or ""),
                suggested_action=str(raw_payload.get("suggested_action") or ""),
                idempotency_key=idempotency_key,
                project_id=project_id,
            ),
        )
        status_code = int(result.pop("_status_code", 202))
        if idempotency_key:
            _complete_idempotency_key(
                ctx.state_dir,
                key=idempotency_key,
                action="loop-action-request",
                payload_hash=payload_hash,
                response=result,
            )
            result["idempotency"] = {"key": idempotency_key, "status": "completed"}
        return JSONResponse(result, status_code=status_code)

    @router.post("/api/projects/{project_id}/loops/{loop_id}/learning/{learning_id}/promotions")
    async def loop_learning_promotion(
        project_id: str,
        loop_id: str,
        learning_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        if authorize_mutation is None:
            return JSONResponse({
                "ok": False,
                "status": "disabled",
                "reason": "loop learning promotion mutation is not configured",
            }, status_code=403)
        auth_error = authorize_mutation(
            "loop-learning-promotion",
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
            web_session_token=request.cookies.get("zf_web_session"),
        )
        if auth_error:
            status_code = int(auth_error.pop("_status_code", 403))
            return JSONResponse(auth_error, status_code=status_code)

        raw_payload = await _request_json(request)
        request_payload = _action_payload({
            **raw_payload,
            "loop_id": loop_id,
            "learning_id": learning_id,
        })
        idempotency_key = str(
            x_idempotency_key
            or raw_payload.get("idempotency_key")
            or raw_payload.get("request_id")
            or "",
        )
        ctx = resolve_ctx(project_id)
        payload_hash = _payload_hash(request_payload)
        if idempotency_key:
            reserved = _reserve_idempotency_key(
                ctx.state_dir,
                key=idempotency_key,
                action="loop-learning-promotion",
                payload_hash=payload_hash,
            )
            status = reserved.get("status")
            if status == "replayed":
                response = dict(reserved.get("response") or {})
                response["idempotency"] = {"key": idempotency_key, "status": "replayed"}
                return JSONResponse(response, status_code=int(response.pop("_status_code", 202)))
            if status == "pending":
                return JSONResponse({
                    "ok": True,
                    "status": "duplicate_pending",
                    "reason": "idempotent loop learning promotion is already pending",
                    "idempotency": {"key": idempotency_key, "status": "pending"},
                }, status_code=202)
            if status == "conflict":
                return JSONResponse({
                    "ok": False,
                    "status": "idempotency_key_conflict",
                    "reason": "idempotency key already used with a different loop learning promotion payload",
                    "idempotency": {"key": idempotency_key, "status": "conflict"},
                }, status_code=409)

        event_log = event_log_from_project(ctx.state_dir, config=ctx.config)
        events = list(enumerate(event_log.read_all()))
        projection = build_loop_projection(events=events, generated_at=_now(), project_id=project_id)
        result = request_loop_learning_promotion(
            projection=projection,
            writer=EventWriter(event_log),
            state_dir=ctx.state_dir,
            request=LoopLearningPromotionRequest(
                loop_id=loop_id,
                learning_id=learning_id,
                target=str(raw_payload.get("target") or ""),
                idempotency_key=idempotency_key,
                project_id=project_id,
            ),
        )
        status_code = int(result.pop("_status_code", 202))
        if idempotency_key:
            _complete_idempotency_key(
                ctx.state_dir,
                key=idempotency_key,
                action="loop-learning-promotion",
                payload_hash=payload_hash,
                response=result,
            )
            result["idempotency"] = {"key": idempotency_key, "status": "completed"}
        return JSONResponse(result, status_code=status_code)

    @router.get("/api/projects/{project_id}/loops/{loop_id}")
    def loop_detail(project_id: str, loop_id: str) -> JSONResponse:
        projection = _projection(project_id)
        for loop in projection.get("loops") or []:
            if isinstance(loop, dict) and loop.get("loop_id") == loop_id:
                return JSONResponse({
                    "schema_version": "loop-detail.v1",
                    "project_id": project_id,
                    "loop": loop,
                    "behaviors": [
                        item for item in projection.get("behaviors") or []
                        if isinstance(item, dict) and item.get("loop_id") == loop_id
                    ],
                    "evals": [
                        item for item in projection.get("evals") or []
                        if isinstance(item, dict) and item.get("loop_id") == loop_id
                    ],
                    "candidates": [
                        item for item in projection.get("candidates") or []
                        if isinstance(item, dict) and item.get("loop_id") == loop_id
                    ],
                    "actions": [
                        item for item in projection.get("actions") or []
                        if isinstance(item, dict) and item.get("loop_id") == loop_id
                    ],
                    "verifications": [
                        item for item in projection.get("verifications") or []
                        if isinstance(item, dict) and item.get("loop_id") == loop_id
                    ],
                    "learning": [
                        item for item in projection.get("learning") or []
                        if isinstance(item, dict) and item.get("loop_id") == loop_id
                    ],
                })
        raise HTTPException(status_code=404, detail="loop not found")

    return router
