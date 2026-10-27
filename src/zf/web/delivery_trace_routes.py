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

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

from fastapi import APIRouter, HTTPException
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
from zf.web.projections.delivery_views import DeliveryView, build_delivery_view
from zf.web.projections.delivery_view_wire import MAX_WIRE_ID_CHARS

_DELIVERY_TRACE_CACHE_VERSION = "v3"
_DELIVERY_VIEW_CACHE_VERSION = "v4"
_DELIVERY_VIEW_TASK_FRESHNESS_FIELD = "_delivery_view_task_freshness"
_DELIVERY_VIEWS = {"overview", "runs", "graph", "work"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_delivery_trace_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    """Build the delivery-trace router. ``resolve_ctx(project_id)`` returns a
    ProjectContext (raising HTTPException for unknown/uninitialized projects)."""
    router = APIRouter()

    def _trace(project_id: str, feature_id: str, *, since_event_id: str = "") -> dict[str, Any]:
        ctx = resolve_ctx(project_id)
        cache_key = (
            f"delivery-trace:{_DELIVERY_TRACE_CACHE_VERSION}:"
            f"{project_id}:{feature_id}:{since_event_id or '-'}"
        )
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

    def _view(
        project_id: str,
        feature_id: str,
        *,
        view: DeliveryView,
        goal_id: str = "",
    ) -> dict[str, Any]:
        """Resolve one v2 base view, independently cached by source watermark."""

        ctx = resolve_ctx(project_id)
        cache_key = (
            f"delivery-view:{_DELIVERY_VIEW_CACHE_VERSION}:"
            f"{project_id}:{feature_id}:{view}"
        )
        if goal_id:
            scope_key = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()
            cache_key = f"{cache_key}:goal:{scope_key}"
        from zf.web.projections.delivery_source import delivery_source_version
        from zf.web.projections.metric_traces import current_seq
        from zf.web.projections.projection_failures import collect_projection_failures
        from zf.web.projections import read_model

        for _attempt in range(2):
            before = delivery_source_version(ctx.state_dir, feature_id, ctx.config)
            try:
                source_seq = current_seq(ctx.state_dir, ctx.config)
            except sqlite3.DatabaseError:
                from zf.web.projections.events import _events_with_seq
                rows = _events_with_seq(ctx.state_dir, config=ctx.config)
                source_seq = rows[-1][0] if rows else 0
            cached = read_model.get_cached_projection(ctx.state_dir, cache_key, source_seq=source_seq)
            if cached is not None:
                cached_source = cached.pop(_DELIVERY_VIEW_TASK_FRESHNESS_FIELD, "")
                if cached_source == before and before == delivery_source_version(ctx.state_dir, feature_id, ctx.config):
                    return cached
            with collect_projection_failures() as failures:
                result = build_delivery_view(
                    state_dir=ctx.state_dir, config=ctx.config, generated_at=_now(),
                    project_id=project_id, feature_id=feature_id, view=view,
                    as_of_seq=source_seq, goal_id=goal_id,
                )
            if not failures and before == delivery_source_version(ctx.state_dir, feature_id, ctx.config):
                try:
                    read_model.set_cached_projection(
                        ctx.state_dir, cache_key, kind="delivery-view", source_seq=source_seq,
                        payload={**result, _DELIVERY_VIEW_TASK_FRESHNESS_FIELD: before},
                    )
                except sqlite3.DatabaseError:
                    pass  # Disposable storage failure does not erase the canonical result.
                return result
        raise HTTPException(status_code=503, detail="Delivery sources changed; retry")

    @router.get("/api/projects/{project_id}/delivery-traces/{feature_id}")
    def delivery_trace(
        project_id: str,
        feature_id: str,
        since_event_id: str = "",
        contract: str = "",
        view: str = "",
        goal_id: str = "",
    ) -> JSONResponse:
        # No-query behavior is the byte/semantic-compatible legacy contract.
        if not contract and not view:
            return JSONResponse(_trace(project_id, feature_id, since_event_id=since_event_id))
        if contract != "v2":
            raise HTTPException(status_code=400, detail="contract must be v2")
        if view not in _DELIVERY_VIEWS:
            raise HTTPException(
                status_code=400,
                detail="view must be overview, runs, graph, or work",
            )
        goal_id = goal_id.strip()
        if goal_id and (
            len(goal_id) > MAX_WIRE_ID_CHARS
            or any(ord(char) < 32 or ord(char) == 127 for char in goal_id)
        ):
            raise HTTPException(
                status_code=400,
                detail=f"goal_id must be an exact ID of at most {MAX_WIRE_ID_CHARS} characters",
            )
        if goal_id and view != "work":
            raise HTTPException(
                status_code=400,
                detail="goal_id is only supported with view=work",
            )
        ctx = resolve_ctx(project_id)
        base = _view(
            project_id,
            feature_id,
            view=cast(DeliveryView, view),
            goal_id=goal_id,
        )
        return JSONResponse(_delivery_view_cursor_projection(
            base,
            state_dir=ctx.state_dir,
            config=ctx.config,
            since_event_id=since_event_id,
        ))

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


def _delivery_view_cursor_projection(
    base: dict[str, Any],
    *,
    state_dir: Any,
    config: Any,
    since_event_id: str,
) -> dict[str, Any]:
    """Attach cursor compatibility without making it part of the v2 cache.

    V2 always returns the complete slim view.  A live caller may still send its
    previous event id, but no event/timeline bodies are repeated as deltas.
    """

    from zf.web.projections import read_model

    last_seq = int(base.get("as_of_seq") or 0)
    last_event_id = str(base.get("as_of_event_id") or "")
    requested = str(since_event_id or "").strip()
    since_seq: int | None = None
    degraded = False
    reason = ""
    if requested:
        try:
            from zf.web.projections.sqlite_reader import connect_readonly
            with connect_readonly(read_model.db_path(state_dir)) as conn:
                row = conn.execute("SELECT seq FROM metric_event WHERE event_id=? ORDER BY seq DESC LIMIT 1",
                                   (requested,)).fetchone()
            hydrated = (row[0], None) if row else None
        except Exception:
            hydrated = None
        if hydrated is None:
            degraded = True
            reason = f"since_event_id {requested} not found in active event log"
        else:
            since_seq = int(hydrated[0])
            if since_seq > last_seq:
                degraded = True
                reason = f"since_event_id {requested} is newer than view watermark"
    new_event_count = (
        max(0, last_seq - since_seq)
        if since_seq is not None and not degraded
        else 0
    )
    result = dict(base)
    result.update({
        "cursor": {
            "schema_version": "delivery-view-cursor.v2",
            "last_event_id": last_event_id,
            "last_seq": last_seq,
            "since_event_id": requested,
            "since_seq": since_seq,
            "new_event_count": new_event_count,
            "has_more": False,
            "degraded": degraded,
            "reason": reason,
            "delta_bodies_included": False,
        },
        "deltas": [],
    })
    return result


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
