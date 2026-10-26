"""Token-gated plan document review; approval continues through existing actions."""

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.plan_artifact_review import PlanArtifactReview
from zf.runtime.workflow_preview_review import PreviewReviewError
from zf.web.projections.request_util import _request_json


def build_plan_artifact_review_router(*, resolve, auth_response):
    router = APIRouter()

    @router.post("/api/projects/{project_id}/plan-artifact-review")
    async def review(project_id: str, request: Request, authorization: str | None = Header(default=None),
                     x_zf_web_token: str | None = Header(default=None)):
        denied = auth_response("chat-orchestrator", request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        ctx = resolve(project_id)
        service = PlanArtifactReview(ctx.state_dir, ctx.project_root,
                    EventWriter(event_log_from_project(ctx.state_dir, config=ctx.config)), ctx.config)
        payload = await _request_json(request)
        try:
            if payload.get("operation") == "context":
                context = service.context(payload)
                result = {"ok": True, **{k: context[k] for k in ("plan_id", "name", "digest", "body", "editable")}}
            elif payload.get("operation") == "apply":
                denied = auth_response("plan-reject", request, authorization, x_zf_web_token)
                if denied is not None:
                    return denied
                if not isinstance(payload.get("draft_ref"), dict):
                    raise PreviewReviewError("invalid_patch", "缺少草稿引用", 422)
                result = await run_in_threadpool(service.apply, payload["draft_ref"])
            elif payload.get("operation", "discuss") == "discuss":
                result = await run_in_threadpool(service.discuss, payload)
            else:
                raise PreviewReviewError("invalid_operation", "不支持的审阅操作", 422)
            code = int(result.pop("_status_code", 200))
            return JSONResponse(redact_obj(result), status_code=code)
        except PreviewReviewError as exc:
            return JSONResponse({"ok": False, "status": exc.code, "reason": str(exc)}, status_code=exc.status)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            return JSONResponse({"ok": False, "status": "invalid_review", "reason": str(exc)}, status_code=422)

    return router
