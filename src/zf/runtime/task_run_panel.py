"""Read-only task run summary panel projection.

The panel is a compact product-facing summary over existing runtime
projections. It does not own execution truth; EventLog, TaskStore, operation
projection, progress projection, and execution route remain the source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task


EventSlice = Sequence[tuple[int, ZfEvent]]

_TERMINAL_TASK_STATES = {"done", "cancelled"}
_TERMINAL_OPERATION_STATES = {"done", "cancelled", "completed"}


def project_task_run_panel(
    *,
    task: Task,
    task_events: EventSlice,
    operations_projection: dict[str, Any] | None = None,
    progress_projection: dict[str, Any] | None = None,
    runs: list[dict[str, Any]] | None = None,
    execution_route: dict[str, Any] | None = None,
    workdir: dict[str, Any] | None = None,
    role_instance: str = "",
    transcript_count: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the task-run-panel.v1 projection from already-read inputs."""

    now = now or datetime.now(timezone.utc)
    events = list(task_events or [])
    operations = _records((operations_projection or {}).get("operations"))
    runs = _records(runs)
    execution_route = execution_route or {}
    progress_projection = progress_projection or {}
    active_operation = _active_operation(operations, task_status=task.status)
    latest_progress = _record(progress_projection.get("latest_progress"))
    heartbeat = _latest_heartbeat(events, role_instance=role_instance)
    health = _health(
        active_operation,
        latest_progress,
        progress_projection,
        heartbeat,
        now=now,
    )
    route_summary = _route_summary(execution_route)
    source_event_ids = _source_event_ids(
        events=events,
        active_operation=active_operation,
        latest_progress=latest_progress,
        execution_route=execution_route,
        heartbeat=heartbeat,
    )
    return redact_obj({
        "schema_version": "task-run-panel.v1",
        "generated_at": now.isoformat(),
        "task_id": task.id,
        "status": task.status,
        "current_stage": route_summary.get("current_stage", "")
            or progress_projection.get("current_phase", "")
            or getattr(task, "phase", ""),
        "current_stage_label": route_summary.get("current_stage_label", ""),
        "active_operation": active_operation,
        "latest_progress": latest_progress,
        "route_summary": route_summary,
        "workdir": workdir or {},
        "role_instance": role_instance or task.assigned_to or "",
        "health": health,
        "counts": {
            "events": len(events),
            "operations": len(operations),
            "runs": len(runs),
            "transcripts": transcript_count,
        },
        "source_event_ids": source_event_ids,
        "empty": not bool(events or operations or runs or latest_progress),
    })


def _active_operation(
    operations: list[dict[str, Any]],
    *,
    task_status: str,
) -> dict[str, Any] | None:
    if not operations:
        return None
    task_terminal = task_status in _TERMINAL_TASK_STATES
    active_candidates = [
        item for item in operations
        if str(item.get("state") or "") not in _TERMINAL_OPERATION_STATES
    ]
    selected = (active_candidates or operations)[-1]
    state = str(selected.get("state") or "")
    return {
        "dispatch_id": _text(selected.get("dispatch_id")),
        "operation_id": _text(selected.get("operation_id")),
        "role": _text(selected.get("role")),
        "instance_id": _text(selected.get("instance_id")),
        "backend": _text(selected.get("backend")),
        "provider_session_ref": _text(selected.get("provider_session_ref")),
        "state": state,
        "active": bool(not task_terminal and state not in _TERMINAL_OPERATION_STATES),
        "last_event": {
            "event_id": _text(selected.get("last_event_id")),
            "type": _text(selected.get("last_event_type")),
            "ts": _text(selected.get("last_event_at")),
        },
        "health": _record(selected.get("health")),
        "resume_packet_path": _text(selected.get("resume_packet_path")),
        "evidence_ref_count": len(_records(selected.get("evidence_refs"))),
    }


def _health(
    active_operation: dict[str, Any] | None,
    latest_progress: dict[str, Any],
    progress_projection: dict[str, Any],
    heartbeat: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    operation_health = _record((active_operation or {}).get("health"))
    context_ratio = _number(latest_progress.get("context_usage_ratio"))
    progress_freshness = _record(progress_projection.get("freshness"))
    heartbeat_ts = _text(heartbeat.get("ts"))
    provider_status = _text(operation_health.get("status")) or "unknown"
    return {
        "provider_status": provider_status,
        "provider_reason": _text(operation_health.get("reason")),
        "provider_requires_operator": bool(operation_health.get("requires_operator")),
        "context_usage_ratio": context_ratio,
        "context_status": _context_status(context_ratio),
        "last_heartbeat_at": heartbeat_ts,
        "last_heartbeat_event_id": _text(heartbeat.get("event_id")),
        "heartbeat_age_seconds": _age_seconds(heartbeat_ts, now=now),
        "last_progress_at": _text(progress_freshness.get("last_progress_at")),
        "last_progress_age_seconds": progress_freshness.get("last_progress_age_sec"),
    }


def _latest_heartbeat(
    events: EventSlice,
    *,
    role_instance: str = "",
) -> dict[str, Any]:
    for _, event in reversed(events):
        if event.type != "worker.heartbeat":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if role_instance:
            instance = _text(payload.get("instance_id")) or event.actor or ""
            if instance != role_instance and event.actor != role_instance:
                continue
        return {
            "event_id": event.id,
            "ts": event.ts,
            "actor": event.actor,
        }
    return {}


def _route_summary(route: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(route, dict):
        return {}
    return {
        "schema_version": _text(route.get("schema_version")),
        "summary": _text(route.get("summary")),
        "status": _text(route.get("status")),
        "current_stage": _text(route.get("current_stage")),
        "current_stage_label": _text(route.get("current_stage_label")),
        "step_count": route.get("step_count", len(_records(route.get("linear")))),
        "parallel": bool(route.get("parallel")),
        "empty": bool(route.get("empty")),
    }


def _source_event_ids(
    *,
    events: EventSlice,
    active_operation: dict[str, Any] | None,
    latest_progress: dict[str, Any],
    execution_route: dict[str, Any],
    heartbeat: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    active_last = _record((active_operation or {}).get("last_event"))
    values.append(_text(active_last.get("event_id")))
    values.append(_text(latest_progress.get("event_id")))
    values.append(_text(heartbeat.get("event_id")))
    for item in _records(execution_route.get("source_events"))[-10:]:
        values.append(_text(item.get("event_id")))
    for _, event in events[-5:]:
        values.append(event.id)
    return list(dict.fromkeys(item for item in values if item))


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _context_status(ratio: float | None) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= 0.9:
        return "critical"
    if ratio >= 0.75:
        return "warning"
    return "ok"


def _age_seconds(ts: str, *, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, round((now - parsed).total_seconds(), 3))
