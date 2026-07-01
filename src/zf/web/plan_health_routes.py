"""Plan 审核与 contract-health Web API(B9/B15,doc 91 §8 + doc 93 §7)。

只读投影路由(sibling,server oversized 纪律);approve/reject 动作走
server 既有统一 action 入口(token gate 上游统一)。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException


def build_plan_health_router(
    *,
    resolve_ctx: Callable[[str], Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/plan/pending")
    def plan_pending(project_id: str) -> dict:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="unknown project")
        from pathlib import Path

        from zf.cli.plan_approval import _checklist, _pending, review_advice
        from zf.core.events.factory import event_log_from_project

        events = list(
            event_log_from_project(ctx.state_dir, config=ctx.config).read_all()
        )
        items = []
        for item in _pending(events):
            ref = str(item.get("task_map_ref") or "")
            entry = dict(item)
            if ref:
                path = Path(ref)
                if not path.is_absolute():
                    root = getattr(ctx, "project_root", None) or Path.cwd()
                    path = Path(root) / ref
                entry["checklist_warnings"] = _checklist(path)
                entry["advice"] = review_advice(path)
            items.append(entry)
        return {"schema_version": "plan-pending.v1", "pending": items}

    @router.get("/api/projects/{project_id}/contract-health")
    def contract_health(project_id: str) -> dict:
        ctx = resolve_ctx(project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="unknown project")
        from zf.core.events.factory import event_log_from_project
        from zf.core.task.contract_health_projection import (
            build_contract_health,
        )
        from zf.core.task.store import TaskStore

        return build_contract_health(
            TaskStore(ctx.state_dir / "kanban.json").list_all(),
            list(
                event_log_from_project(
                    ctx.state_dir, config=ctx.config,
                ).read_all()
            ),
        )

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

    return router
