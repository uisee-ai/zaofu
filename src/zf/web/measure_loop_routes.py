"""Measure Loop Web API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.core.events.factory import event_log_from_project
from zf.runtime.dispatch_diagnostics import build_dispatch_diagnostics
from zf.runtime.loop_projection import build_loop_projection
from zf.runtime.measure_loop_projection import build_measure_loop_projection
from zf.runtime.stage_loop_projection import build_loop_view


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_measure_loop_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/measure/loops")
    def measure_loops(project_id: str, feature_id: str = "", lens: str = "all") -> JSONResponse:
        ctx = resolve_ctx(project_id)
        source_seq = 0
        cache_key = f"measure-loop:{project_id}:{feature_id or '-'}:{lens or 'all'}"
        try:
            from zf.web.projections import read_model

            source_seq = read_model.current_projected_seq(ctx.state_dir, config=ctx.config)
            cached = read_model.get_cached_projection(
                ctx.state_dir,
                cache_key,
                source_seq=source_seq,
            )
            if cached is not None:
                return JSONResponse(cached)
        except Exception:
            source_seq = 0
        events = list(enumerate(event_log_from_project(ctx.state_dir, config=ctx.config).read_all()))
        generated_at = _now()
        loop_projection = build_loop_projection(events=events, generated_at=generated_at, project_id=project_id)
        dispatch = build_dispatch_diagnostics(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
        )
        projection = build_measure_loop_projection(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
            project_id=project_id,
            feature_id=feature_id,
            lens=lens,
            generated_at=generated_at,
            events=events,
            loop_projection=loop_projection,
            dispatch_diagnostics=dispatch,
        )
        if source_seq:
            try:
                from zf.web.projections import read_model

                read_model.set_cached_projection(
                    ctx.state_dir,
                    cache_key,
                    kind="measure-loop",
                    source_seq=source_seq,
                    payload=projection,
                )
            except Exception:
                pass
        return JSONResponse(projection)

    @router.get("/api/projects/{project_id}/loop-view")
    def loop_view(project_id: str) -> JSONResponse:
        ctx = resolve_ctx(project_id)
        source_seq = 0
        cache_key = f"loop-view:{project_id}"
        try:
            from zf.web.projections import read_model

            source_seq = read_model.current_projected_seq(ctx.state_dir, config=ctx.config)
            cached = read_model.get_cached_projection(
                ctx.state_dir,
                cache_key,
                source_seq=source_seq,
            )
            if cached is not None:
                return JSONResponse(cached)
        except Exception:
            source_seq = 0
        # 事件解析走 web 层指纹派生缓存(与 snapshot/measure-loop 共享,
        # 冷路径不再重复付全量 read_all;RF 批教训)
        try:
            from zf.web.projections.events import _events_with_seq

            events = _events_with_seq(ctx.state_dir, config=ctx.config)
        except Exception:
            events = None
        projection = build_loop_view(
            ctx.state_dir,
            config=ctx.config,
            project_root=ctx.project_root,
            project_id=project_id,
            events=events,
            generated_at=_now(),
        )
        if source_seq:
            try:
                from zf.web.projections import read_model

                read_model.set_cached_projection(
                    ctx.state_dir,
                    cache_key,
                    kind="loop-view",
                    source_seq=source_seq,
                    payload=projection,
                )
            except Exception:
                pass
        return JSONResponse(projection)

    return router
