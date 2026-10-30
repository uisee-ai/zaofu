"""Read-only, Request-scoped Workflow workbench endpoint."""

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, Response

from zf.web.projections.workflow_workbench_cache import (
    WorkbenchSourceChanged, read_workbench_projection, load_structure_snapshot, structure_snapshot_is_current,
)


def build_workbench_router(*, resolve, detail):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}/graph")
    def graph(project_id: str, request_id: str, as_of: int = Query(0, ge=0),
              if_none_match: str | None = Header(default=None),
              structure_version: str | None = Header(default=None, alias="X-ZF-Structure-Generation")):
        try:
            context = resolve(project_id)
            snapshot = load_structure_snapshot(context, project_id, request_id, as_of, structure_version) if structure_version else None
            if structure_version and snapshot is None:
                return JSONResponse({"ok": False, "status": "structure_changed"}, status_code=409,
                                    headers={"Cache-Control": "no-store"})
            result = read_workbench_projection(context, project_id, request_id, as_of,
                lambda pid, rid, **kwargs: detail(pid, rid, context=context, as_mapping=True, **kwargs),
                if_none_match=if_none_match, event_snapshot=snapshot)
            if snapshot is not None and not structure_snapshot_is_current(context, request_id, snapshot):
                return JSONResponse({"ok": False, "status": "structure_changed"}, status_code=409,
                                    headers={"Cache-Control": "no-store"})
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

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}/graph/execution")
    def graph_execution(project_id: str, request_id: str, as_of: int = Query(0, ge=0),
                        if_none_match: str | None = Header(default=None),
                        structure_version: str = Header(alias="X-ZF-Structure-Generation")):
        if not structure_version:
            return JSONResponse({"ok": False, "status": "structure_generation_required"}, status_code=400)
        try:
            context = resolve(project_id)
            snapshot = load_structure_snapshot(context, project_id, request_id, as_of, structure_version) if structure_version else None
            if structure_version and snapshot is None:
                return JSONResponse({"ok": False, "status": "structure_changed"}, status_code=409,
                                    headers={"Cache-Control": "no-store"})
            result = read_workbench_projection(context, project_id, request_id, as_of,
                lambda pid, rid, **kwargs: detail(pid, rid, context=context, as_mapping=True, **kwargs),
                if_none_match=if_none_match, execution_only=True, event_snapshot=snapshot)
            if snapshot is not None and not structure_snapshot_is_current(context, request_id, snapshot):
                return JSONResponse({"ok": False, "status": "structure_changed"}, status_code=409,
                                    headers={"Cache-Control": "no-store"})
        except (WorkbenchSourceChanged, OSError, ValueError):
            return JSONResponse({"ok": False, "status": "source_unavailable"}, status_code=503,
                                headers={"Retry-After": "1", "Cache-Control": "no-store"})
        if isinstance(result, JSONResponse):
            return result
        value, etag = result
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse({**value, "generation": structure_version}, headers=headers)

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}/graph/node")
    def graph_structure_node(project_id: str, request_id: str, node_id: str = Query(min_length=1, max_length=512),
                             as_of: int = Query(0, ge=0),
                             structure_version: str = Header(alias="X-ZF-Structure-Generation")):
        try:
            context = resolve(project_id)
            if not structure_version:
                return JSONResponse({"ok": False, "status": "structure_generation_required"}, status_code=400)
            snapshot = load_structure_snapshot(context, project_id, request_id, as_of, structure_version) if structure_version else None
            if structure_version and snapshot is None:
                return JSONResponse({"ok": False, "status": "structure_changed"}, status_code=409,
                                    headers={"Cache-Control": "no-store"})
            result = read_workbench_projection(context, project_id, request_id, as_of,
                lambda pid, rid, **kwargs: detail(pid, rid, context=context, as_mapping=True, **kwargs),
                structure_only=True, structure_node_id=node_id, event_snapshot=snapshot)
            if snapshot is not None and not structure_snapshot_is_current(context, request_id, snapshot):
                return JSONResponse({"ok": False, "status": "structure_changed"}, status_code=409,
                                    headers={"Cache-Control": "no-store"})
        except (WorkbenchSourceChanged, OSError, ValueError):
            return JSONResponse({"ok": False, "status": "source_unavailable"}, status_code=503,
                                headers={"Retry-After": "1", "Cache-Control": "no-store"})
        if isinstance(result, JSONResponse):
            return result
        value, _etag = result
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @router.get("/api/projects/{project_id}/workflow-requests/{request_id}/graph/structure")
    def graph_structure(project_id: str, request_id: str, as_of: int = Query(0, ge=0),
                        if_none_match: str | None = Header(default=None)):
        try:
            context = resolve(project_id)
            result = read_workbench_projection(context, project_id, request_id, as_of,
                lambda pid, rid, **kwargs: detail(pid, rid, context=context, as_mapping=True, **kwargs),
                if_none_match=if_none_match, structure_only=True)
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
