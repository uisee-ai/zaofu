"""Project workflow-request HTTP routes.

The routes live outside the already oversized Web server module. They remain
thin adapters over the canonical CLI/request services and receive auth/session
callbacks from the server so this module does not own Web security policy.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from zf.cli.flow import (
    apply_flow_submit,
    build_flow_intake,
    build_flow_intent,
    build_flow_submit_preview,
)
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.web.projections.request_util import _request_json
from zf.web.projections.workspace import _resolve_api_project


def workflow_request_strings(value: object) -> list[str]:
    """Normalize repeatable workflow-request fields from JSON surfaces."""

    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def build_workflow_request_router(
    *,
    default_project_id: str,
    default_state_dir: Path,
    default_config: Any,
    default_project_root: Path,
    mutation_auth_error: Callable[..., dict | None],
    session_cookie: Callable[[Request], str | None],
) -> APIRouter:
    router = APIRouter()

    def resolve(project_id: str):
        return _resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=default_state_dir,
            default_config=default_config,
            default_project_root=default_project_root,
        )

    def auth_response(
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
        status_code = int(body.pop("_status_code", 403))
        return JSONResponse(body, status_code=status_code)

    @router.post("/api/projects/{project_id}/workflow-intake")
    async def project_workflow_intake(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-intake", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        context = resolve(project_id)
        payload = await _request_json(request)
        request_id = str(payload.get("request_id") or "")
        output = payload.get("output")
        if not output and request_id:
            output = context.project_root / "docs" / "intake" / f"{request_id}.md"
        result = build_flow_intake(
            kind=str(payload.get("kind") or payload.get("request_kind") or "auto"),
            source_ref=str(payload.get("from") or payload.get("source_ref") or ""),
            objective=str(
                payload.get("objective")
                or payload.get("message")
                or payload.get("reason")
                or ""
            ),
            source_root=str(payload.get("source_root") or ""),
            target_root=str(payload.get("target_root") or payload.get("target") or ""),
            backend=str(payload.get("backend") or "codex"),
            lanes=int(payload.get("lanes") or payload.get("requested_lanes") or 0),
            project_id=project_id,
            project_name=str(payload.get("project_name") or ""),
            request_id=request_id,
            source=str(payload.get("source") or "web"),
            created_by=str(payload.get("created_by") or "web"),
            channel_id=str(payload.get("channel_id") or ""),
            thread_id=str(payload.get("thread_id") or ""),
            acceptance=tuple(workflow_request_strings(payload.get("acceptance"))),
            constraints=tuple(workflow_request_strings(payload.get("constraints"))),
            open_questions=tuple(workflow_request_strings(payload.get("open_questions"))),
            output=Path(str(output)).expanduser() if output else None,
        )
        return JSONResponse({"ok": True, "status": "intake_created", "result": result})

    @router.post("/api/projects/{project_id}/workflow-classify")
    async def project_workflow_classify(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-classify", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        resolve(project_id)
        payload = await _request_json(request)
        intake_ref = str(payload.get("intake") or payload.get("intake_ref") or "").strip()
        if not intake_ref:
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "intake_ref is required"},
                status_code=422,
            )
        result = build_flow_intent(
            intake_path=Path(intake_ref).expanduser(),
            explicit_kind=str(payload.get("kind") or "auto"),
        )
        return JSONResponse({"ok": True, "status": "classified", "result": result})

    @router.post("/api/projects/{project_id}/workflow-clarify")
    async def project_workflow_clarify(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-intake", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        context = resolve(project_id)
        if context.config is None:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "project_not_initialized",
                    "reason": "project zf.yaml is required before requirement clarification",
                },
                status_code=409,
            )
        payload = await _request_json(request)
        intake_ref = str(payload.get("intake") or payload.get("intake_ref") or "").strip()
        if not intake_ref:
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "intake_ref is required"},
                status_code=422,
            )
        from zf.cli.flow import _load_manifest_for_intake
        from zf.runtime.workflow_requests import revise_workflow_request

        manifest_path, _manifest = _load_manifest_for_intake(Path(intake_ref).expanduser())
        if manifest_path is None:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "manifest_not_found",
                    "reason": "workflow input manifest not found for intake",
                },
                status_code=404,
            )
        writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
        result = revise_workflow_request(
            context.state_dir,
            manifest_path,
            actor=str(payload.get("actor") or payload.get("requested_by") or "web"),
            objective=str(payload["objective"]) if "objective" in payload else None,
            source_root=str(payload["source_root"]) if "source_root" in payload else None,
            target_root=(
                str(payload.get("target_root") or payload.get("target") or "")
                if "target_root" in payload or "target" in payload
                else None
            ),
            acceptance=(
                workflow_request_strings(payload.get("acceptance"))
                if "acceptance" in payload
                else None
            ),
            constraints=(
                workflow_request_strings(payload.get("constraints"))
                if "constraints" in payload
                else None
            ),
            open_questions=(
                workflow_request_strings(payload.get("open_questions"))
                if "open_questions" in payload
                else None
            ),
            confirm=bool(payload.get("confirm")),
            writer=writer,
        )
        status = str(result.get("status") or "")
        return JSONResponse(
            {"ok": status != "clarifying", "status": status, "result": result},
            status_code=200 if status != "clarifying" else 409,
        )

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}")
    def project_workflow_request_detail(project_id: str, request_id: str) -> JSONResponse:
        from zf.runtime.workflow_requests import load_workflow_request

        result = load_workflow_request(resolve(project_id).state_dir, request_id)
        if not result:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "not_found",
                    "reason": f"workflow request {request_id!r} not found",
                },
                status_code=404,
            )
        return JSONResponse({"ok": True, "status": result.get("status"), "result": result})

    @router.post("/api/projects/{project_id}/workflow-submit")
    async def project_workflow_submit(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = auth_response("workflow-submit", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        context = resolve(project_id)
        payload = await _request_json(request)
        intake_ref = str(payload.get("intake") or payload.get("intake_ref") or "").strip()
        if not intake_ref:
            return JSONResponse(
                {"ok": False, "status": "invalid_payload", "reason": "intake_ref is required"},
                status_code=422,
            )
        config_ref = Path(
            str(payload.get("config") or payload.get("config_ref") or context.config_path)
        ).expanduser()
        apply = bool(payload.get("apply"))
        builder = apply_flow_submit if apply else build_flow_submit_preview
        result = builder(
            config_path=config_ref,
            intake_path=Path(intake_ref).expanduser(),
            flow_kind=str(payload.get("kind") or ""),
            task_id=str(payload.get("task_id") or ""),
            pattern_id=str(payload.get("pattern_id") or ""),
            requested_by=str(payload.get("requested_by") or "web"),
            reason=str(payload.get("reason") or ""),
            allow_missing_env=bool(payload.get("allow_missing_env")),
        )
        status = str(result.get("status") or "")
        code = 202 if apply and status != "STOP" else 200
        if status == "STOP":
            code = 409
        return JSONResponse(
            {"ok": status != "STOP", "status": status, "applied": apply, "result": result},
            status_code=code,
        )

    return router


__all__ = ["build_workflow_request_router", "workflow_request_strings"]
