"""Read-only Task Pipeline projection routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.runtime.task_pipeline_projection import read_task_pipeline_projection
from zf.runtime.task_pipeline_dispatch_summary import build_dispatch_summary


def build_task_pipeline_router(
    *,
    resolve_ctx: Callable[[str], Any],
    default_state_dir: Path,
    default_project_root: Path,
    default_config: Any,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/task-pipeline/summary")
    def project_dispatch_summary(project_id: str, workflow_run_id: str = "") -> JSONResponse:
        context = resolve_ctx(project_id)
        projection = read_task_pipeline_projection(
            context.state_dir, project_root=context.project_root, config=context.config,
        )
        return JSONResponse(build_dispatch_summary(projection, workflow_run_id=workflow_run_id))

    @router.get("/api/task-pipeline/summary")
    def dispatch_summary(workflow_run_id: str = "") -> JSONResponse:
        projection = read_task_pipeline_projection(
            default_state_dir, project_root=default_project_root, config=default_config,
        )
        return JSONResponse(build_dispatch_summary(projection, workflow_run_id=workflow_run_id))

    @router.get("/api/projects/{project_id}/task-pipeline")
    def project_task_pipeline(project_id: str) -> JSONResponse:
        context = resolve_ctx(project_id)
        return JSONResponse(read_task_pipeline_projection(
            context.state_dir,
            project_root=context.project_root,
            config=context.config,
        ))

    @router.get("/api/task-pipeline")
    def task_pipeline() -> JSONResponse:
        return JSONResponse(read_task_pipeline_projection(
            default_state_dir,
            project_root=default_project_root,
            config=default_config,
        ))

    return router


__all__ = ["build_task_pipeline_router"]
