"""Runtime resource Web API routes."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException


def build_runtime_resource_router(
    *,
    resolve_ctx: Callable[[str], Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/runtime/resources")
    def runtime_resources(project_id: str) -> dict:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="unknown project")
        from zf.runtime.runtime_resources import build_runtime_resource_projection

        return build_runtime_resource_projection(
            ctx.state_dir,
            project_root=getattr(ctx, "project_root", None),
            config=getattr(ctx, "config", None),
        )

    @router.get("/api/runtime/resources")
    def default_runtime_resources() -> dict:
        return runtime_resources("default")

    return router
