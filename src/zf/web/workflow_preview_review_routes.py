"""Token-gated Preview review surface, reusing the headless chat adapter."""

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from zf.core.config.loader import ConfigError
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.workflow_preview_review import PreviewReviewError, WorkflowPreviewReview
from zf.web.projections.request_util import _request_json


def build_preview_review_router(*, resolve, auth_response) -> APIRouter:
    router = APIRouter()

    @router.post("/api/projects/{project_id}/workflow-preview-review")
    async def review(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ):
        denied = auth_response(
            "chat-orchestrator", request, authorization, x_zf_web_token
        )
        if denied is not None:
            return denied
        context = resolve(project_id)
        payload = await _request_json(request)
        service = WorkflowPreviewReview(
            context.state_dir,
            context.project_root,
            EventWriter(
                event_log_from_project(context.state_dir, config=context.config)
            ),
        )
        operation = payload.get("operation", "discuss")
        try:
            if operation == "context":
                result = {"ok": True, **service.public_context(payload)}
            elif operation == "discuss":
                result = await run_in_threadpool(service.discuss, payload)
            elif operation == "apply":
                denied = auth_response(
                    "workflow-intake", request, authorization, x_zf_web_token
                )
                if denied is not None:
                    return denied
                if not isinstance(payload.get("draft_ref"), dict):
                    raise PreviewReviewError(
                        "invalid_patch", "draft_ref is required", 422
                    )
                result = await run_in_threadpool(service.apply, payload["draft_ref"])
            else:
                raise PreviewReviewError(
                    "invalid_operation", "Unknown review operation", 422
                )
            return JSONResponse(redact_obj(result))
        except PreviewReviewError as exc:
            return JSONResponse(
                {"ok": False, "status": exc.code, "reason": str(exc)},
                status_code=exc.status,
            )
        except (ConfigError, ValueError, OSError, KeyError, TypeError) as exc:
            return JSONResponse(
                {"ok": False, "status": "review_invalid", "reason": str(exc)},
                status_code=422,
            )

    return router
