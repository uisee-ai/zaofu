"""Overview pulse projection (overview-pulse.v1) — doc 49/70/73/79/80.

Backs the Overview triage bands: RUN PULSE (liveness), TASK FLOW (pipeline
rates + blocked side pocket), attention aging, and the dispatch why-not
footnote. Read-only derived projection over EventLog/TaskStore — rebuildable,
never a second control plane. Sibling APIRouter (doc 68 E1a) mounted by
create_app via ``build_overview_pulse_router``; this module never imports
back from server.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.kanban_projection import kanban_column_projection
from zf.core.task.store import TaskStore
from zf.runtime.dispatch_diagnostics import (
    build_dispatch_notifications,
    build_runtime_loop_health,
)
from zf.runtime.runtime_resources import build_runtime_resource_projection

SCHEMA_VERSION = "overview-pulse.v1"
PULSE_BUCKETS = 12
PULSE_BUCKET_SECONDS = 300.0  # 12 x 5m = trailing hour
FLOW_WINDOW_HOURS = 24.0
COOLDOWN_LOOKBACK_SECONDS = 1800.0
BACKEDGE_REWORK_KIND = "workflow_stage_backedge"

_TODO_STATUSES = {"backlog", "ready", "todo"}
_VERIFY_STATUSES = {"verify", "in_review", "review"}

_ESCALATE_ACK_TYPES = {"human.resolved", "remediation.escalated_acked"}
_REMEDIATION_OPEN_TYPES = {"remediation.classified", "remediation.routed"}
_REMEDIATION_CLOSE_TYPES = {
    "remediation.consumed",
    "remediation.recovered",
    "remediation.escalated_acked",
}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(ts: str | None, now: datetime) -> float | None:
    parsed = _parse_ts(ts)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


def _events_in_window(events: Iterable[ZfEvent], now: datetime, hours: float) -> list[ZfEvent]:
    limit = hours * 3600.0
    out = []
    for event in events:
        age = _age_seconds(event.ts, now)
        if age is not None and age <= limit:
            out.append(event)
    return out


def _run_pulse(events: list[ZfEvent], now: datetime) -> dict[str, Any]:
    last_event_age = _age_seconds(events[-1].ts, now) if events else None
    buckets = [0] * PULSE_BUCKETS
    horizon = PULSE_BUCKETS * PULSE_BUCKET_SECONDS
    for event in events:
        age = _age_seconds(event.ts, now)
        if age is None or age >= horizon:
            continue
        # oldest bucket first so the UI reads left -> right as time advances
        index = PULSE_BUCKETS - 1 - int(age // PULSE_BUCKET_SECONDS)
        buckets[index] += 1

    streak = 0
    for event in reversed(events):
        if event.type == "worker.respawn.failed":
            streak += 1
            continue
        if event.type in {"worker.spawned", "workdir.prepared", "worker.respawned"}:
            break
        if streak:
            break
    cooldown_instances: list[str] = []
    for event in events:
        if event.type != "worker.respawn.cooldown":
            continue
        age = _age_seconds(event.ts, now)
        if age is not None and age <= COOLDOWN_LOOKBACK_SECONDS:
            instance = str(event.payload.get("instance_id") or event.payload.get("role") or "")
            if instance and instance not in cooldown_instances:
                cooldown_instances.append(instance)
    return {
        "last_event_age_seconds": last_event_age,
        "events_per_bucket": buckets,
        "bucket_seconds": PULSE_BUCKET_SECONDS,
        "respawn_failed_streak": streak,
        "respawn_cooldown_instances": cooldown_instances,
    }


def _status_bucket(status: str) -> str:
    if status in _TODO_STATUSES:
        return "todo"
    if status in _VERIFY_STATUSES:
        return "verify"
    if status in {"in_progress", "blocked", "done"}:
        return status
    return "other"


def _task_bucket(task: object) -> str:
    projection = kanban_column_projection(task)  # type: ignore[arg-type]
    if projection.column == "ready":
        return "todo"
    if projection.column == "testing":
        return "verify"
    if projection.column in {"in_progress", "blocked", "done"}:
        return projection.column
    return _status_bucket(str(getattr(task, "status", "")))


def _writer_capacity(config: ZfConfig | None) -> int | None:
    if config is None:
        return None
    total = 0
    for role in getattr(config, "roles", []) or []:
        if str(getattr(role, "role_kind", "")) == "writer":
            total += max(1, int(getattr(role, "replicas", 1) or 1))
    return total or None


def _task_flow(
    state_dir: Path,
    config: ZfConfig | None,
    events: list[ZfEvent],
    now: datetime,
) -> dict[str, Any]:
    try:
        tasks = TaskStore(state_dir / "kanban.json").list_all()
    except Exception:
        tasks = []
    counts: dict[str, int] = {"todo": 0, "in_progress": 0, "verify": 0, "blocked": 0, "done": 0, "other": 0}
    last_task_event_ts: dict[str, str] = {}
    for event in events:
        if event.task_id:
            last_task_event_ts[event.task_id] = event.ts
    oldest: dict[str, float | None] = {}
    blocked: list[dict[str, Any]] = []
    for task in tasks:
        bucket = _task_bucket(task)
        counts[bucket] = counts.get(bucket, 0) + 1
        anchor_ts = last_task_event_ts.get(task.id) or getattr(task, "created_at", "")
        age = _age_seconds(anchor_ts, now)
        if bucket in {"todo", "in_progress", "verify", "blocked"} and age is not None:
            current = oldest.get(bucket)
            if current is None or age > current:
                oldest[bucket] = age
        if bucket == "blocked":
            blockers = ", ".join(getattr(task, "blocked_by", []) or [])
            blocked.append({
                "task_id": task.id,
                "reason": f"blocked by {blockers}" if blockers else "blocked",
                "age_seconds": age,
            })
    blocked.sort(key=lambda row: row.get("age_seconds") or 0.0, reverse=True)

    window = _events_in_window(events, now, FLOW_WINDOW_HOURS)
    hours = FLOW_WINDOW_HOURS

    def _rate(count: int) -> float:
        return round(count / hours, 3) if hours else 0.0

    dispatched = sum(1 for e in window if e.type == "task.dispatched")
    impl_done = sum(1 for e in window if e.type == "dev.build.done")
    done = sum(1 for e in window if e.type == "task.done")
    backedge = sum(
        1
        for e in window
        if str(e.payload.get("rework_kind") or "") == BACKEDGE_REWORK_KIND
    )
    return {
        "columns": counts,
        "oldest_age_seconds": {key: oldest.get(key) for key in ("todo", "in_progress", "verify", "blocked")},
        "transitions_per_hour": {
            "todo_to_in_progress": _rate(dispatched),
            "in_progress_to_verify": _rate(impl_done),
            "verify_to_done": _rate(done),
        },
        "window_hours": hours,
        "wip": {
            "used": counts.get("in_progress", 0),
            "capacity": _writer_capacity(config),
        },
        "rework_backedge_per_hour": _rate(backedge),
        "blocked_side_pocket": blocked[:3],
        "done_gate": "judge AND-closure",
    }


def _attention(events: list[ZfEvent], now: datetime) -> dict[str, Any]:
    escalations: dict[str, ZfEvent] = {}
    acked_event_ids: set[str] = set()
    acked_tasks: set[str] = set()
    acked_correlations: set[str] = set()
    for event in events:
        if event.type == "human.escalate":
            escalations[event.id] = event
        elif event.type in _ESCALATE_ACK_TYPES:
            if event.causation_id:
                acked_event_ids.add(event.causation_id)
            if event.task_id:
                acked_tasks.add(event.task_id)
            if event.correlation_id:
                acked_correlations.add(event.correlation_id)
    unacked = [
        e for e in escalations.values()
        if e.id not in acked_event_ids
        and not (e.task_id and e.task_id in acked_tasks)
        and not (e.correlation_id and e.correlation_id in acked_correlations)
    ]
    oldest_age = None
    for event in unacked:
        age = _age_seconds(event.ts, now)
        if age is not None and (oldest_age is None or age > oldest_age):
            oldest_age = age

    open_by_key: dict[str, str] = {}
    for event in events:
        key = event.correlation_id or event.id
        if event.type in _REMEDIATION_OPEN_TYPES:
            tier = str(event.payload.get("tier") or event.payload.get("route") or "unrouted")
            open_by_key[key] = tier
        elif event.type in _REMEDIATION_CLOSE_TYPES:
            open_by_key.pop(event.correlation_id or "", None)
    remediation_open: dict[str, int] = {}
    for tier in open_by_key.values():
        remediation_open[tier] = remediation_open.get(tier, 0) + 1

    safe_halt_active = False
    for event in events:
        if event.type == "runtime.safe_halted":
            safe_halt_active = True
        elif event.type == "runtime.resumed":
            safe_halt_active = False
    sm_stuck = sum(1 for e in events if e.type == "remediation.sm_stuck_observed")
    return {
        "unacked_escalations": len(unacked),
        "oldest_unacked_escalation_seconds": oldest_age,
        "remediation_open_by_tier": remediation_open,
        "sm_stuck_observed": sm_stuck,
        "safe_halt_active": safe_halt_active,
    }


def _why_not(state_dir: Path, events: list[ZfEvent], loop: dict[str, Any]) -> dict[str, Any]:
    try:
        notifications = build_dispatch_notifications(state_dir, loop=loop, events=events)
    except Exception:
        notifications = []
    summary = "dispatching_normally"
    if notifications:
        summary = str(notifications[0].get("kind") or "see_notifications")
    return {
        "summary": summary,
        "notifications": notifications[:3],
    }


def _sessions(state_dir: Path, config: ZfConfig | None, events: list[ZfEvent]) -> dict[str, Any]:
    """Active agent/worker session summary for RUN PULSE.

    Same-source: reuses doc 96 P5 ``build_runtime_resource_projection`` (no
    second session counter). Skips terminal-excerpt file reads
    (``max_terminal_sessions=0``) and the tmux subprocess (``tmux_output=""``)
    so the live Overview poll stays cheap. Glance counts only — the per-session
    table lives on the Runtime Resources page.
    """
    try:
        projection = build_runtime_resource_projection(
            state_dir,
            config=config,
            events=events,
            max_terminal_sessions=0,
            tmux_output="",
        )
    except Exception:
        return {"active": 0, "total": 0, "stale": 0, "by_state": {}, "by_backend": {}}
    sessions = projection.get("provider_sessions", []) or []
    by_state: dict[str, int] = {}
    by_backend: dict[str, int] = {}
    active = 0
    stale = 0
    for session in sessions:
        if session.get("stale"):
            stale += 1
            continue
        active += 1
        state = str(session.get("state") or "unknown")
        backend = str(session.get("backend") or "unknown")
        by_state[state] = by_state.get(state, 0) + 1
        by_backend[backend] = by_backend.get(backend, 0) + 1
    return {
        "active": active,
        "total": len(sessions),
        "stale": stale,
        "by_state": by_state,
        "by_backend": by_backend,
    }


def build_overview_pulse(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    try:
        events = event_log_from_project(state_dir, config=config).read_days(2)
    except Exception:
        events = []
    loop = build_runtime_loop_health(state_dir, events=events, now=current)
    return {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": current.isoformat(),
        "run_pulse": {**_run_pulse(events, current), "loop": {
            "status": loop.get("status"),
            "age_seconds": loop.get("age_seconds"),
        }, "sessions": _sessions(state_dir, config, events)},
        "task_flow": _task_flow(state_dir, config, events, current),
        "attention": _attention(events, current),
        "why_not": _why_not(state_dir, events, loop),
    }


def build_overview_pulse_router(*, resolve_ctx: Callable[[str], Any]) -> APIRouter:
    """Overview pulse router. ``resolve_ctx(project_id)`` returns a
    ProjectContext (raising HTTPException for unknown/uninitialized projects)."""
    router = APIRouter()

    @router.get("/api/projects/{project_id}/overview-pulse")
    def overview_pulse(project_id: str) -> JSONResponse:
        ctx = resolve_ctx(project_id)
        return JSONResponse(build_overview_pulse(ctx.state_dir, config=ctx.config))

    return router
