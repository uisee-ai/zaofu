"""Read-only Web adapters for the runtime-neutral artifact query service."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from zf.core.config.schema import ZfConfig
from zf.runtime.artifact_query import ArtifactQueryService


def build_artifact_query_router(
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig | None,
) -> APIRouter:
    router = APIRouter()

    def service() -> ArtifactQueryService:
        return ArtifactQueryService(
            state_dir=state_dir,
            project_root=project_root,
            config=config,
        )

    @router.get("/api/artifacts/catalog")
    def artifact_catalog(
        kind: str = "",
        ref: str = "",
        task_id: str = "",
        run_id: str = "",
        attempt_id: str = "",
        operation_id: str = "",
        package_id: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> JSONResponse:
        query = service()
        return JSONResponse(query.catalog_list(
            context=query.context(
                actor="web",
                purpose="catalog",
                mode="canonical",
                limit=limit,
                offset=offset,
            ),
            kind=kind,
            ref=ref,
            task_id=task_id,
            run_id=run_id,
            attempt_id=attempt_id,
            operation_id=operation_id,
            package_id=package_id,
        ))

    @router.get("/api/artifacts/catalog/{identity}")
    def artifact_catalog_detail(identity: str) -> JSONResponse:
        query = service()
        result = query.catalog_show(
            identity,
            context=query.context(
                actor="web",
                purpose="catalog",
                mode="canonical",
                limit=1,
            ),
        )
        if result.get("item") is None:
            raise HTTPException(404, f"artifact {identity!r} not found")
        return JSONResponse(result)

    @router.get("/api/artifacts/lineage/{subject_kind}/{subject_id}")
    def artifact_lineage(
        subject_kind: str,
        subject_id: str,
        limit: int = 200,
    ) -> JSONResponse:
        query = service()
        return JSONResponse(query.lineage(
            subject_kind=subject_kind,
            subject_id=subject_id,
            context=query.context(
                actor="web",
                purpose="lineage",
                mode="canonical",
                limit=limit,
            ),
        ))

    @router.get("/api/tasks/{task_id}/artifacts")
    def task_artifacts(task_id: str, limit: int = 200) -> JSONResponse:
        query = service()
        return JSONResponse(query.task_artifacts(
            task_id,
            context=query.context(
                actor="web",
                purpose="task-artifacts",
                mode="canonical",
                limit=limit,
            ),
        ))

    @router.get("/api/attempts/{attempt_id}")
    def attempt_artifacts(attempt_id: str) -> JSONResponse:
        query = service()
        return JSONResponse(query.attempt_inspect(
            attempt_id,
            context=query.context(
                actor="web",
                purpose="attempt-inspect",
                mode="canonical",
            ),
        ))

    @router.get("/api/attempts/{attempt_id}/missing-reads")
    def attempt_missing_reads(attempt_id: str) -> JSONResponse:
        query = service()
        return JSONResponse(query.attempt_missing_reads(
            attempt_id,
            context=query.context(
                actor="web",
                purpose="attempt-inspect",
                mode="canonical",
            ),
        ))

    @router.get("/api/runs/{run_id}/plan-package-advisory")
    def plan_package_advisory(run_id: str) -> JSONResponse:
        query = service()
        return JSONResponse(query.plan_package_projection(
            run_id,
            context=query.context(
                actor="web",
                purpose="plan-package-advisory",
                mode="advisory",
            ),
        ))

    return router


__all__ = ["build_artifact_query_router"]
