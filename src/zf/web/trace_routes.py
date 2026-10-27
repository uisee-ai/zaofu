"""Project-scoped legacy and bounded Trace HTTP routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from zf.web.projections.events import _trace_detail, _traces
from zf.web.projections.trace_pages import (
    TraceCursorError,
    trace_detail_page,
    trace_list_page,
)
from zf.web.projections.trace_spans import trace_span_page
from zf.web.projections.projection_failures import collect_projection_failures



def _complete_read(build: Callable[..., dict], *args, **kwargs) -> dict:
    with collect_projection_failures() as failures:
        result = build(*args, **kwargs)
    if failures:
        raise HTTPException(503, "Trace sources unavailable; retry")
    return result

def build_trace_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    """Build read-only Trace routes against the caller's project resolver."""

    router = APIRouter()

    @router.get("/api/projects/{project_id}/traces")
    def project_traces(
        project_id: str,
        contract: str = "",
        limit: int = 50,
        cursor: str = "",
    ) -> dict[str, Any]:
        context = resolve_ctx(project_id)
        if not contract:
            traces = _traces(context.state_dir, config=context.config)
            return {
                "schema_version": "traces.v1",
                "is_derived_projection": True,
                "items": traces,
                "traces": traces,
            }
        if contract != "v2":
            raise HTTPException(400, "unsupported Trace list contract")
        try:
            return _complete_read(trace_list_page,
                context.state_dir,
                limit=limit,
                cursor=cursor,
                config=context.config,
            )
        except TraceCursorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/projects/{project_id}/traces/{trace_id}")
    def project_trace_detail(
        project_id: str,
        trace_id: str,
        contract: str = "",
        limit: int = 80,
        cursor: str = "",
    ) -> dict[str, Any]:
        context = resolve_ctx(project_id)
        if not contract:
            return _trace_detail(
                context.state_dir,
                trace_id,
                config=context.config,
            )
        if contract != "v2":
            raise HTTPException(400, "unsupported Trace detail contract")
        try:
            return _complete_read(trace_detail_page,
                context.state_dir,
                trace_id,
                limit=limit,
                cursor=cursor,
                config=context.config,
            )
        except TraceCursorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/projects/{project_id}/traces/{trace_id}/spans")
    def project_trace_spans(
        project_id: str,
        trace_id: str,
        contract: str = "v1",
        limit: int = 50,
        cursor: str = "",
        focus_span_id: str = "",
    ) -> dict[str, Any]:
        context = resolve_ctx(project_id)
        if contract != "v1":
            raise HTTPException(400, "unsupported Trace spans contract")
        try:
            return _complete_read(trace_span_page,
                context.state_dir,
                trace_id,
                limit=limit,
                cursor=cursor,
                focus_span_id=focus_span_id,
                config=context.config,
            )
        except TraceCursorError as exc:
            raise HTTPException(400, str(exc)) from exc

    return router


__all__ = ["build_trace_router"]
