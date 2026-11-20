"""Read-only chapter navigation; never emits worker read or approval facts."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from zf.runtime.plan_document import PlanDocumentError, document_index, document_section, document_source


def build_plan_document_router(resolve_ctx):
    router = APIRouter()

    def read(project_id, plan_id, name, sha256, operation, **kwargs):
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(404, "unknown project")
        from zf.core.events.factory import event_log_from_project

        try:
            events = list(event_log_from_project(ctx.state_dir, config=ctx.config).read_all())
            path = document_source(ctx.state_dir, events, plan_id=plan_id, name=name,
                                   sha256=sha256, project_root=ctx.project_root)
            return JSONResponse(operation(ctx.state_dir, path, sha256, **kwargs),
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        except PlanDocumentError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "bound source is unavailable") from exc
        except PermissionError as exc:
            raise HTTPException(403, "source reading is not permitted") from exc
        except UnicodeError as exc:
            raise HTTPException(415, "source is not valid UTF-8") from exc

    base = "/api/projects/{project_id}/plans/{plan_id}/artifacts/{name}/document"

    @router.get(base)
    def index(project_id: str, plan_id: str, name: str,
              sha256: str = Query(pattern=r"^[a-f0-9]{64}$"),
              offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
              anchor: str = Query("", max_length=256), q: str = Query("", max_length=256)):
        return read(project_id, plan_id, name, sha256, document_index, offset=offset, limit=limit, anchor=anchor, q=q)

    @router.get(base + "/sections/{section}")
    def section(project_id: str, plan_id: str, name: str, section: int,
                sha256: str = Query(pattern=r"^[a-f0-9]{64}$"), page: int = Query(0, ge=0)):
        return read(project_id, plan_id, name, sha256, document_section, section=section, page=page)

    return router
