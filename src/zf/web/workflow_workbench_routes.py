"""Read-only, Request-scoped Workflow workbench endpoint."""

import json
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from zf.core.security.redaction import redact_obj
from zf.runtime.sdlc_graph import build_sdlc_graph
from zf.web.projections.workflow_workbench_sources import read_workbench_sources


def build_workbench_router(*, resolve, detail):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}/graph")
    def graph(project_id: str, request_id: str, as_of: int = Query(0, ge=0)):
        response = detail(project_id, request_id)
        if response.status_code != 200:
            return response
        inputs = read_workbench_sources(resolve(project_id), json.loads(response.body), as_of=as_of)
        return JSONResponse(redact_obj(build_sdlc_graph(inputs)))

    return router
