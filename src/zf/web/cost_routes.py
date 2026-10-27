"""Read-only project cost projection routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.web.projections.common import _cost as read_cost
from zf.web.projections.projection_failures import collect_projection_failures


def build_cost_router(
    *,
    resolve_ctx: Callable[[str], Any],
    default_state_dir: Path,
) -> APIRouter:
    router = APIRouter()

    def response(state_dir: Path) -> JSONResponse:
        with collect_projection_failures() as failures:
            value = read_cost(state_dir)
        if failures:
            return JSONResponse({"status": "unavailable"}, status_code=503,
                                headers={"Retry-After": "1", "Cache-Control": "no-store"})
        return JSONResponse(value)

    @router.get("/api/projects/{project_id}/cost")
    def project_cost(project_id: str) -> JSONResponse:
        return response(resolve_ctx(project_id).state_dir)

    @router.get("/api/cost")
    def cost() -> JSONResponse:
        return response(default_state_dir)

    return router


__all__ = ["build_cost_router"]
