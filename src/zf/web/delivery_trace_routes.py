"""Delivery-trace Web API routes (doc 68 S3 / doc 65 P1).

Read-only endpoints exposing the already-landed projections (doc 65 P0 +
doc 68 S1): delivery-trace.v1 / execution-graph.v1 / drift-report.v1 /
workflow-run.v1. Implemented as a sibling APIRouter mounted via
``include_router`` rather than appended to ``server.py``'s create_app — the
router-as-sibling pattern (doc 68 E1a) so new route families stop growing the
monolith. Self-contained: create_app passes a ``resolve_ctx`` closure, so this
module never imports back from server.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.runtime.delivery_projection_common import event_status, payload
from zf.runtime.delivery_thick_trace import build_delivery_thick_trace
from zf.runtime.delivery_trace_resolve import (
    resolve_delivery_trace, resolve_drift_report, resolve_execution_graph,
)
from zf.runtime.loop_projection import (
    build_loop_projection,
    related_loop_ids_for_delivery_trace,
)
from zf.runtime.run_chain import build_run_chain
from zf.runtime.workflow_run import build_workflow_run


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_delivery_trace_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    """Build the delivery-trace router. ``resolve_ctx(project_id)`` returns a
    ProjectContext (raising HTTPException for unknown/uninitialized projects)."""
    router = APIRouter()

    def _trace(project_id: str, feature_id: str, *, since_event_id: str = "") -> dict[str, Any]:
        ctx = resolve_ctx(project_id)
        cache_key = f"delivery-trace:{project_id}:{feature_id}:{since_event_id or '-'}"
        source_seq = 0
        try:
            from zf.web.projections import read_model

            source_seq = read_model.current_projected_seq(ctx.state_dir, config=ctx.config)
            cached = read_model.get_cached_projection(
                ctx.state_dir,
                cache_key,
                source_seq=source_seq,
            )
            if cached is not None:
                return cached
        except Exception:
            source_seq = 0
        trace = resolve_delivery_trace(
            state_dir=ctx.state_dir, config=ctx.config, generated_at=_now(),
            project_id=project_id, feature_id=feature_id,
        )
        events = list(enumerate(event_log_from_project(ctx.state_dir, config=ctx.config).read_all()))
        trace.update(_delivery_cursor_projection(events, since_event_id=since_event_id))
        dag = getattr(getattr(ctx.config, "workflow", None), "dag", None)
        trace["run_chain"] = build_run_chain(  # 2026-06-11 S-D (run-chain.v1)
            events, stage_order=list(getattr(dag, "stage_order", []) or []))
        loop_projection = build_loop_projection(
            events=events,
            generated_at=_now(),
            project_id=project_id,
        )
        related_loop_ids = related_loop_ids_for_delivery_trace(
            trace=trace,
            loop_projection=loop_projection,
        )
        trace["related_loop_ids"] = related_loop_ids
        trace["related_loop_count"] = len(related_loop_ids)
        trace["thick_trace"] = build_delivery_thick_trace(
            trace=trace,
            events=events,
            generated_at=_now(),
            project_id=project_id,
        )
        trace["thick_trace"]["related_loop_ids"] = related_loop_ids
        trace["thick_trace"]["related_loop_count"] = len(related_loop_ids)
        if source_seq:
            try:
                from zf.web.projections import read_model

                read_model.set_cached_projection(
                    ctx.state_dir,
                    cache_key,
                    kind="delivery-trace",
                    source_seq=source_seq,
                    payload=trace,
                )
            except Exception:
                pass
        return trace

    @router.get("/api/projects/{project_id}/delivery-traces/{feature_id}")
    def delivery_trace(project_id: str, feature_id: str, since_event_id: str = "") -> JSONResponse:
        return JSONResponse(_trace(project_id, feature_id, since_event_id=since_event_id))

    @router.get("/api/projects/{project_id}/delivery-traces/{feature_id}/thick")
    def delivery_thick_trace(project_id: str, feature_id: str, since_event_id: str = "") -> JSONResponse:
        return JSONResponse(_trace(
            project_id, feature_id, since_event_id=since_event_id,
        ).get("thick_trace", {}))

    @router.get("/api/projects/{project_id}/delivery-traces/{feature_id}/causation/{event_id}")
    def causation_chain(project_id: str, feature_id: str, event_id: str) -> JSONResponse:
        """T-knife 2 (2026-06-11): walk causation_id back to the trigger so the
        Run Graph can highlight the full causal path of any node/edge."""
        ctx = resolve_ctx(project_id)
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        chain = log.get_causation_chain(event_id)
        return JSONResponse({
            "schema_version": "causation-chain.v1",
            "feature_id": feature_id,
            "chain": [
                {"id": e.id, "type": e.type, "ts": e.ts, "task_id": e.task_id}
                for e in chain
            ],
        })

    @router.get("/api/projects/{project_id}/delivery-traces/{feature_id}/execution-graph")
    def execution_graph(project_id: str, feature_id: str) -> JSONResponse:
        ctx = resolve_ctx(project_id)
        return JSONResponse(resolve_execution_graph(
            state_dir=ctx.state_dir, config=ctx.config, feature_id=feature_id))

    @router.get("/api/projects/{project_id}/delivery-traces/{feature_id}/drift-report")
    def drift_report(project_id: str, feature_id: str) -> JSONResponse:
        ctx = resolve_ctx(project_id)
        return JSONResponse(resolve_drift_report(
            state_dir=ctx.state_dir, config=ctx.config, feature_id=feature_id))

    @router.get("/api/projects/{project_id}/workflow-runs/{fanout_id}")
    def workflow_run(project_id: str, fanout_id: str) -> JSONResponse:
        ctx = resolve_ctx(project_id)
        event_log = event_log_from_project(ctx.state_dir, config=ctx.config)
        events = list(enumerate(event_log.read_all()))
        return JSONResponse(build_workflow_run(fanout_id=fanout_id, events=events))

    return router


def _delivery_cursor_projection(
    events: list[tuple[int, ZfEvent]],
    *,
    since_event_id: str,
) -> dict[str, Any]:
    """Build additive poll/cursor metadata for Delivery Trace.

    This is deliberately a projection over the append-only event log. It does
    not change the delivery-trace schema_version or mutate runtime state.
    """

    last_seq = events[-1][0] if events else -1
    last_event_id = events[-1][1].id if events else ""
    since_event_id = since_event_id.strip()
    degraded = False
    reason = ""
    since_seq: int | None = None
    selected: list[tuple[int, ZfEvent]] = []
    if since_event_id:
        for seq, event in events:
            if event.id == since_event_id:
                since_seq = seq
                break
        if since_seq is None:
            degraded = True
            reason = f"since_event_id {since_event_id} not found in active event log"
        else:
            selected = [(seq, event) for seq, event in events if seq > since_seq]
    cursor = {
        "schema_version": "delivery-cursor.v1",
        "last_event_id": last_event_id,
        "last_seq": last_seq,
        "since_event_id": since_event_id,
        "since_seq": since_seq,
        "new_event_count": len(selected),
        "has_more": False,
        "degraded": degraded,
        "reason": reason,
    }
    deltas = _delivery_deltas(selected)
    if degraded:
        deltas.insert(0, {
            "schema_version": "delivery-delta.v1",
            "type": "cursor.degraded",
            "status": "degraded",
            "event_id": "",
            "seq": last_seq,
            "reason": reason,
        })
    return {"cursor": cursor, "deltas": deltas}


def _delivery_deltas(events: list[tuple[int, ZfEvent]]) -> list[dict[str, Any]]:
    return [
        _delivery_delta(seq, event)
        for seq, event in events[-200:]
    ]


def _delivery_delta(seq: int, event: ZfEvent) -> dict[str, Any]:
    data = payload(event)
    stage_id = str(data.get("stage_id") or "")
    fanout_id = str(data.get("fanout_id") or "")
    task_id = str(event.task_id or data.get("task_id") or "")
    return {
        "schema_version": "delivery-delta.v1",
        "type": _delta_type(event, stage_id=stage_id, fanout_id=fanout_id),
        "seq": seq,
        "event_id": event.id,
        "event_type": event.type,
        "status": event_status(event),
        "task_id": task_id,
        "stage_id": stage_id,
        "fanout_id": fanout_id,
        "ts": event.ts,
    }


def _delta_type(event: ZfEvent, *, stage_id: str, fanout_id: str) -> str:
    if event.type.startswith("autoresearch."):
        return "autoresearch.changed"
    if event.type.startswith("fanout.child."):
        return "fanout.child_changed"
    if event.type.startswith("fanout.") or fanout_id:
        return "run.status_changed"
    if event.type.startswith("workflow.") or stage_id:
        return "stage.status_changed"
    if event.task_id:
        return "task.event"
    return "event.appended"
