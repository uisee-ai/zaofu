"""Read-only dispatch health and notification projections.

This module does not mutate runtime truth. It derives operator-facing
diagnostics from kernel state so ready-but-idle failures are visible without
turning diagnostics into another control plane.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


_LOOP_HEALTH_EVENTS = frozenset({
    "loop.started",
    "orchestrator.decision.recorded",
    "orchestrator.round.complete",
    "orchestrator.idle",
    "orchestrator.tick.failed",
})

_DISPATCHABLE_STATES = frozenset({"idle", "spawned_idle", "available"})
_HANDOFF_IDLE_STATES = frozenset({
    "awaiting_review",
    "awaiting_test",
    "awaiting_qa",
    "awaiting_judge",
    "completion_pending",
})


def build_dispatch_diagnostics(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = 180.0,
) -> dict[str, Any]:
    events = _read_recent_events(state_dir, config=config)
    ready_tasks = _ready_tasks(state_dir)
    workers = build_worker_availability(
        state_dir,
        config=config,
        project_root=project_root,
        events=events,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    loop = build_runtime_loop_health(
        state_dir,
        events=events,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    notifications = build_dispatch_notifications(
        state_dir,
        ready_tasks=ready_tasks,
        workers=workers,
        loop=loop,
        events=events,
    )
    return {
        "loop": loop,
        "worker_availability": workers,
        "notifications": notifications,
        "ready_task_count": len(ready_tasks),
        "dispatchable_worker_count": sum(
            1 for worker in workers if worker.get("dispatchable") is True
        ),
    }


def build_runtime_loop_health(
    state_dir: Path,
    *,
    events: Iterable[ZfEvent] | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = 180.0,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    loop_lock = state_dir / "loop.lock"
    lock_present = loop_lock.exists()
    last_event: ZfEvent | None = None
    for event in events or []:
        if event.type in _LOOP_HEALTH_EVENTS:
            last_event = event
    age = _age_seconds(last_event.ts if last_event is not None else "", current)
    stale = age is not None and age > stale_after_seconds
    if lock_present and not stale:
        status = "running"
    elif lock_present and stale:
        status = "stale"
    elif last_event is not None and not stale:
        status = "recently_active"
    else:
        status = "not_running"
    return {
        "status": status,
        "loop_lock": str(loop_lock),
        "lock_present": lock_present,
        "last_event_type": last_event.type if last_event else "",
        "last_event_at": last_event.ts if last_event else "",
        "age_seconds": age,
        "stale_after_seconds": stale_after_seconds,
        "stale": stale,
    }


def build_worker_availability(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    events: Iterable[ZfEvent] | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = 180.0,
) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    root = project_root or state_dir.parent
    registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", str(root))
    meta = registry.instance_meta()
    records: dict[str, dict[str, Any]] = {}
    if config is not None:
        for role in config.roles:
            instance_id = role.instance_id or role.name
            records[instance_id] = {
                "instance_id": instance_id,
                "parent_role": role.name,
                "role_kind": role.role_kind,
                "backend": role.backend,
                "origin": "static",
            }
    for instance_id, values in meta.items():
        record = records.setdefault(instance_id, {
            "instance_id": instance_id,
            "parent_role": _parent_role(instance_id),
            "role_kind": str(values.get("role_kind") or ""),
            "backend": str(values.get("backend") or ""),
            "origin": "runtime",
        })
        record.update({
            "backend": record.get("backend") or str(values.get("backend") or ""),
            "spawned_at": str(values.get("spawned_at") or ""),
            "last_heartbeat_at": str(values.get("last_heartbeat_at") or ""),
            "session_path": str(values.get("session_path") or ""),
        })
        payload = values.get("last_heartbeat_payload")
        if isinstance(payload, dict):
            record["heartbeat_state"] = str(payload.get("state") or "")
            record["heartbeat_task_id"] = str(payload.get("current_task_id") or "")
    event_state, event_task, event_ts = _worker_state_from_events(events or [])
    active_by_assignee = _active_task_by_assignee(state_dir)
    terminal_task_ids = _terminal_task_ids(state_dir)
    known_task_ids = _known_task_ids(state_dir)
    for instance_id, record in records.items():
        state = (
            str(record.get("heartbeat_state") or "")
            or event_state.get(instance_id, "")
            or "unknown"
        )
        active_task = (
            active_by_assignee.get(instance_id, "")
            or str(record.get("heartbeat_task_id") or "")
            or event_task.get(instance_id, "")
        )
        if active_task in terminal_task_ids:
            active_task = ""
            if state in _HANDOFF_IDLE_STATES:
                state = "idle"
        elif active_task and active_task not in known_task_ids:
            record["stale_task_id"] = active_task
            active_task = ""
            if state not in _DISPATCHABLE_STATES:
                state = "idle"
        last_at = (
            str(record.get("last_heartbeat_at") or "")
            or event_ts.get(instance_id, "")
        )
        age = _age_seconds(last_at, current)
        heartbeat_stale = age is not None and age > stale_after_seconds
        if active_task and state in {"idle", "available", "spawned_idle"}:
            availability = "inconsistent"
        elif active_task:
            availability = "busy"
        elif state in _DISPATCHABLE_STATES:
            availability = "dispatchable"
        elif heartbeat_stale and state in {"busy", "blocked"}:
            availability = "stale"
        elif state == "unknown":
            availability = "unknown"
        else:
            availability = state
        record.update({
            "state": state,
            "active_task": active_task,
            "last_seen_at": last_at,
            "age_seconds": age,
            "heartbeat_stale": heartbeat_stale,
            "availability": availability,
            "dispatchable": availability == "dispatchable",
        })
    return sorted(records.values(), key=lambda item: str(item["instance_id"]))


def build_dispatch_notifications(
    state_dir: Path,
    *,
    ready_tasks: list[Task] | None = None,
    workers: list[dict[str, Any]] | None = None,
    loop: dict[str, Any] | None = None,
    events: Iterable[ZfEvent] | None = None,
) -> list[dict[str, Any]]:
    ready_tasks = ready_tasks if ready_tasks is not None else _ready_tasks(state_dir)
    workers = workers if workers is not None else build_worker_availability(state_dir)
    loop = loop if loop is not None else build_runtime_loop_health(state_dir)
    events = list(events or [])
    notifications: list[dict[str, Any]] = []
    if ready_tasks and loop.get("status") in {"not_running", "stale"}:
        notifications.append({
            "kind": "loop_unavailable_for_ready_tasks",
            "severity": "warning",
            "task_id": ready_tasks[0].id,
            "ready_task_count": len(ready_tasks),
            "reason": str(loop.get("status") or ""),
        })
    dispatchable = [
        worker for worker in workers if worker.get("dispatchable") is True
    ]
    for task in ready_tasks:
        preferred_role = _preferred_role_for_task(task)
        candidates = [
            worker for worker in dispatchable
            if not preferred_role or worker.get("parent_role") == preferred_role
        ]
        if candidates:
            continue
        notifications.append({
            "kind": "ready_task_no_dispatchable_worker",
            "severity": "warning",
            "task_id": task.id,
            "title": task.title,
            "preferred_role": preferred_role,
            "reason": "no idle worker matches the task dispatch role",
        })
    dispatch_index_by_task = _latest_dispatch_index_by_task(events)
    skipped_by_task: dict[str, int] = {}
    for idx, event in enumerate(events):
        if event.type != "orchestrator.dispatch_skipped":
            continue
        task_id = str(event.task_id or "")
        if not task_id:
            continue
        if idx < dispatch_index_by_task.get(task_id, -1):
            continue
        skipped_by_task[task_id] = skipped_by_task.get(task_id, 0) + 1
    for task_id, count in sorted(skipped_by_task.items()):
        if count < 2:
            continue
        notifications.append({
            "kind": "repeated_dispatch_skipped",
            "severity": "warning",
            "task_id": task_id,
            "count": count,
            "reason": "dispatch skipped repeatedly in recent events",
        })
    assigned_without_dispatched = _assigned_without_dispatched(events)
    for task_id, assigned in assigned_without_dispatched.items():
        notifications.append({
            "kind": "assigned_without_dispatch",
            "severity": "error",
            "task_id": task_id,
            "assignee": assigned,
            "reason": "task.assigned exists without matching task.dispatched",
        })
    return notifications


def _read_recent_events(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
) -> list[ZfEvent]:
    try:
        return event_log_from_project(state_dir, config=config, warn=False).read_days(1)
    except Exception:
        return []


def _ready_tasks(state_dir: Path) -> list[Task]:
    try:
        return TaskStore(state_dir / "kanban.json").ready()
    except Exception:
        return []


def _active_task_by_assignee(state_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for task in TaskStore(state_dir / "kanban.json").list_all():
            if task.assigned_to and task.status not in {"done", "cancelled"}:
                out[task.assigned_to] = task.id
    except Exception:
        return {}
    return out


def _terminal_task_ids(state_dir: Path) -> set[str]:
    try:
        return {
            task.id
            for task in TaskStore(state_dir / "kanban.json").list_all_with_archive()
            if task.status in {"done", "cancelled"}
        }
    except Exception:
        return set()


def _known_task_ids(state_dir: Path) -> set[str]:
    try:
        return {
            task.id
            for task in TaskStore(state_dir / "kanban.json").list_all_with_archive()
        }
    except Exception:
        return set()


def _worker_state_from_events(
    events: Iterable[ZfEvent],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    state: dict[str, str] = {}
    task: dict[str, str] = {}
    ts: dict[str, str] = {}
    for event in events:
        actor = str(event.actor or "")
        payload = event.payload if isinstance(event.payload, dict) else {}
        instance_id = str(payload.get("instance_id") or actor or "")
        if not instance_id:
            continue
        if event.type == "worker.state.changed":
            value = str(payload.get("to") or payload.get("state") or "")
            if value:
                state[instance_id] = value
            task_id = str(event.task_id or payload.get("task_id") or "")
            if task_id:
                task[instance_id] = task_id
            elif value in _DISPATCHABLE_STATES:
                task.pop(instance_id, None)
            ts[instance_id] = event.ts
        elif event.type == "worker.heartbeat":
            value = str(payload.get("state") or "")
            if value:
                state[instance_id] = value
            task_id = str(payload.get("current_task_id") or event.task_id or "")
            if task_id:
                task[instance_id] = task_id
            elif value in _DISPATCHABLE_STATES:
                task.pop(instance_id, None)
            ts[instance_id] = event.ts
    return state, task, ts


def _assigned_without_dispatched(events: Iterable[ZfEvent]) -> dict[str, str]:
    assigned: dict[str, tuple[str, int]] = {}
    dispatched: dict[str, int] = {}
    for idx, event in enumerate(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        if not task_id:
            continue
        if event.type == "task.assigned":
            assigned[task_id] = (str(
                payload.get("assignee")
                or payload.get("assigned_to")
                or event.actor
                or ""
            ), idx)
        elif event.type in {"task.dispatched", "fanout.child.dispatched"}:
            dispatched[task_id] = idx
    return {
        task_id: assignee
        for task_id, (assignee, assigned_idx) in assigned.items()
        if dispatched.get(task_id, -1) < assigned_idx
    }


def _latest_dispatch_index_by_task(events: Iterable[ZfEvent]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, event in enumerate(events):
        if event.type not in {"task.dispatched", "fanout.child.dispatched"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        if task_id:
            out[task_id] = idx
    return out


def _preferred_role_for_task(task: Task) -> str:
    contract = task.contract
    value = str(getattr(contract, "owner_role", "") or "").strip()
    if value:
        return value
    if task.assigned_to:
        return _parent_role(task.assigned_to)
    return ""


def _parent_role(instance_id: str) -> str:
    return instance_id.split("-", 1)[0] if "-" in instance_id else instance_id


def _age_seconds(raw: str, now: datetime) -> float | None:
    ts = _parse_ts(raw)
    if ts is None:
        return None
    return max(0.0, (now - ts).total_seconds())


def _parse_ts(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
