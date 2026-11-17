"""Bound, paginated human material metadata; no worker-read or acceptance facts."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from zf.core.events.factory import event_log_from_project
from zf.runtime.plan_preview_artifacts import preview_artifact_path
from zf.runtime.plan_preview_summary import build_plan_preview_summary


def build_plan_material_router(resolve_ctx):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/plans/{plan_id}/preview/materials")
    def catalog(project_id: str, plan_id: str, offset: int = Query(0, ge=0),
                limit: int = Query(50, ge=1, le=100), request_event_id: str = ""):
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(404, "unknown project")
        summary = build_plan_preview_summary(ctx.state_dir,
            list(event_log_from_project(ctx.state_dir, config=ctx.config).read_all()),
            plan_id=plan_id, project_root=ctx.project_root)
        if not summary.get("ok"):
            raise HTTPException(404, "unknown plan")
        if request_event_id and request_event_id != summary["requested_event_id"]:
            raise HTTPException(409, "approval identity changed; reopen the catalog")
        if summary.get("artifacts_error"):
            raise HTTPException(409, "material bindings unavailable or changed")
        ports = summary["artifacts"]
        items = []
        for port in ports[offset:offset + limit]:
            size, state = None, "unknown"
            try:
                path = preview_artifact_path(port["ref"], state_dir=ctx.state_dir, project_root=ctx.project_root)
                info = path.stat()
                if path.is_file():
                    size = info.st_size
                    state = "available" if size else "empty"
                else:
                    state = "unsupported"
            except FileNotFoundError:
                state = "unavailable"
            except (PermissionError, ValueError):
                state = "forbidden"
            except OSError:
                pass
            media_type = {".md": "text/markdown", ".markdown": "text/markdown", ".json": "application/json",
                          ".txt": "text/plain", ".webm": "video/webm"}.get(Path(port["ref"]).suffix.lower(), "unknown")
            items.append({**port, "media_type": media_type, "size_bytes": size, "observed_state": state,
                "integrity": "unread", "version_binding": "declared" if port.get("sha256") else "unknown",
                "source_event_id": summary["source_event_id"], "supersedes": None})
        next_offset = offset + len(items) if offset + len(items) < len(ports) else None
        return JSONResponse({"plan_id": plan_id, "request_event_id": summary["requested_event_id"],
            "items": items, "offset": offset, "total": len(ports), "next_offset": next_offset,
            "catalog_complete": next_offset is None, "scope": "metadata_only"}, headers={"Cache-Control": "no-store"})

    return router
