"""Read-only, Request-scoped Workflow workbench endpoint."""

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, Response

from zf.web.projections.workflow_workbench_cache import (
    WorkbenchSourceChanged, read_workbench_projection,
)


def build_workbench_router(*, resolve, detail):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}/graph")
    def graph(project_id: str, request_id: str, as_of: int = Query(0, ge=0),
              if_none_match: str | None = Header(default=None)):
        try:
            context = resolve(project_id)
            result = read_workbench_projection(context, project_id, request_id, as_of,
                lambda pid, rid, **kwargs: detail(pid, rid, context=context, **kwargs),
                if_none_match=if_none_match)
        except (WorkbenchSourceChanged, OSError, ValueError):
            return JSONResponse({"ok": False, "status": "source_unavailable",
                                 "reason": "Workflow graph source changed or is unavailable; retry."},
                                status_code=503, headers={"Retry-After": "1", "Cache-Control": "no-store"})
        if isinstance(result, JSONResponse):
            return result
        value, etag = result
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(value, headers=headers)

    return router
