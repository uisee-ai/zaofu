"""Plan 审核 + operator inbox / plan preview Web API(B15,doc 93 §7)。

只读投影路由(sibling,server oversized 纪律);approve/reject 动作走
server 既有统一 action 入口(token gate 上游统一)。
(contract-health 投影已退役 2026-07-09:纯展示不 gate,全绿为噪音,
唯一出口 Inbox 已移除。)
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse


def build_plan_health_router(
    *,
    resolve_ctx: Callable[[str], Any],
) -> APIRouter:
    router = APIRouter()
    from zf.web.plan_document_routes import build_plan_document_router

    router.include_router(build_plan_document_router(resolve_ctx))

    @router.get("/api/projects/{project_id}/plan/pending")
    def plan_pending(project_id: str, workflow_run_id: str | None = None) -> dict:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="unknown project")
        from zf.cli.plan_approval import _pending, review_advice
        from zf.core.events.factory import event_log_from_project
        from zf.runtime.plan_preview_artifacts import preview_artifact_path

        events = list(
            event_log_from_project(ctx.state_dir, config=ctx.config).read_all()
        )
        items = []
        for item in _pending(events, workflow_run_id):
            ref = str(item.get("task_map_ref") or "")
            entry = dict(item)
            if ref:
                try:
                    path = preview_artifact_path(
                        ref, state_dir=ctx.state_dir,
                        project_root=getattr(ctx, "project_root", None),
                    )
                    advice = review_advice(path)
                except (OSError, ValueError) as exc:
                    advice = {
                        "advice": "reject", "binding": False,
                        "note": "Advice is non-binding; the operator makes the decision.",
                        "checklist_warnings": [f"[X] task_map unavailable: {exc}"],
                    }
                entry["checklist_warnings"] = advice["checklist_warnings"]
                entry["advice"] = advice
            items.append(entry)
        return {"schema_version": "plan-pending.v1", "pending": items}

    @router.get("/api/projects/{project_id}/operator/inbox")
    def operator_inbox(project_id: str) -> dict:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="unknown project")
        try:
            from zf.web.projections import read_model

            projected = read_model.operator_inbox(
                ctx.state_dir,
                config=ctx.config,
                project_root=getattr(ctx, "project_root", None),
            )
            if projected is not None:
                return projected
        except Exception:
            pass
        from zf.core.events.factory import event_log_from_project
        from zf.runtime.operator_inbox import build_operator_inbox

        events = list(
            event_log_from_project(ctx.state_dir, config=ctx.config).read_all()
        )
        return build_operator_inbox(
            ctx.state_dir,
            events,
            project_root=getattr(ctx, "project_root", None),
        )

    @router.get("/api/operator/inbox")
    def default_operator_inbox() -> dict:
        return operator_inbox("default")

    @router.get("/api/projects/{project_id}/plans/{plan_id}/preview")
    def plan_preview(project_id: str, plan_id: str) -> dict:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="unknown project")
        from zf.core.events.factory import event_log_from_project
        from zf.runtime.operator_plan_preview import build_plan_preview

        events = list(
            event_log_from_project(ctx.state_dir, config=ctx.config).read_all()
        )
        preview = build_plan_preview(
            ctx.state_dir,
            events,
            plan_id=plan_id,
            project_root=getattr(ctx, "project_root", None),
        )
        if preview.get("ok") is False:
            raise HTTPException(status_code=404, detail=str(preview.get("reason") or "plan not found"))
        return preview

    @router.get("/api/plans/{plan_id}/preview")
    def default_plan_preview(plan_id: str) -> dict:
        return plan_preview("default", plan_id)

    @router.get("/api/projects/{project_id}/plans/{plan_id}/artifacts/{name}")
    def plan_artifact(project_id: str, plan_id: str, name: str,
                      sha256: str = Query("", pattern=r"^$|^[a-f0-9]{64}$")) -> PlainTextResponse:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="unknown project")
        from zf.core.events.factory import event_log_from_project
        from zf.runtime.operator_plan_preview import read_plan_preview_attachment
        from zf.runtime.plan_preview_sources import PlanSourceError

        try:
            content = read_plan_preview_attachment(
                ctx.state_dir, event_log_from_project(ctx.state_dir, config=ctx.config).read_all(),
                plan_id=plan_id, name=name, project_root=getattr(ctx, "project_root", None),
            )
        except PlanSourceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if sha256:
            import hashlib

            if hashlib.sha256(content.encode()).hexdigest() != sha256:
                raise HTTPException(409, "source version changed; reload the preview")
        return PlainTextResponse(content, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @router.get("/api/projects/{project_id}/plans/{plan_id}/input-review")
    def plan_input_review(project_id: str, plan_id: str, task: str = "", offset: int = 0, limit: int = 5) -> dict:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(404, "unknown project")
        from zf.core.events.factory import event_log_from_project
        from zf.runtime.plan_preview_sources import bound_plan_sources, PlanSourceError
        from zf.runtime.plan_preview_artifacts import read_preview_artifact
        from zf.runtime.plan_input_review import inspect_plan_inputs, PORTS
        import json
        try:
            events = event_log_from_project(ctx.state_dir, config=ctx.config).read_all()
            _, bindings, _ = bound_plan_sources(ctx.state_dir, events, plan_id=plan_id, project_root=ctx.project_root)
            artifacts = {row["name"]: json.loads(read_preview_artifact(row["ref"], state_dir=ctx.state_dir,
                project_root=ctx.project_root, digest=row["sha256"])) for row in bindings if row["name"] in PORTS}
            report = inspect_plan_inputs(artifacts, task_id=task, offset=offset, limit=limit, bindings=bindings, plan_id=plan_id)
            return report
        except PlanSourceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
