"""Task-flow and per-role efficiency stats for the Web cockpit.

Read-only derivations over events + kanban tasks: 24h/7d done counts,
oldest-age of in-flight columns, and per-role done/duration/rework/respawn
rollups. Done semantics mirror ``MetricsCollector`` (task status == done);
the kernel collector stays the only owner of the 12-metric snapshot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from zf.core.task.schema import Task
from zf.runtime.delivery_projection_common import EventSlice, payload

_DONE_EVENT_TYPES = {"task.done.accepted", "task.status_changed"}
_REWORK_EVENT_TYPES = {"task.rework.requested"}
_RESPAWN_EVENT_TYPES = {"worker.respawned", "worker.respawn.completed"}


def build_task_flow_stats(
    events: EventSlice,
    tasks: dict[str, Task],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    done_at = _task_done_timestamps(events)
    day_buckets = [0] * 7
    done_24h = 0
    for ts in done_at.values():
        age = (now - ts).total_seconds()
        if age < 0:
            continue
        if age <= 86400:
            done_24h += 1
        days_ago = int(age // 86400)
        if days_ago < 7:
            day_buckets[6 - days_ago] += 1
    return {
        "schema_version": "task-flow-stats.v1",
        "done_24h": done_24h,
        "done_7d": day_buckets,
        "throughput_per_hour_24h": round(done_24h / 24.0, 3),
        "oldest_in_progress_seconds": _oldest_age(tasks, "in_progress", now),
        "oldest_blocked_seconds": _oldest_age(tasks, "blocked", now),
    }


def build_role_efficiency(
    events: EventSlice,
    tasks: dict[str, Task],
    *,
    now: datetime | None = None,
    window_days: int = 7,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    dispatched_at: dict[str, datetime] = {}
    rows: dict[str, dict[str, Any]] = {}

    def row(role: str) -> dict[str, Any]:
        return rows.setdefault(role, {
            "role": role, "done": 0, "duration_minutes_total": 0.0,
            "duration_samples": 0, "rework": 0, "respawn": 0,
        })

    done_at = _task_done_timestamps(events)
    for _seq, event in events:
        ts = _parse_ts(event.ts)
        if ts is None:
            continue
        if event.type == "task.dispatched" and event.task_id:
            dispatched_at.setdefault(str(event.task_id), ts)
        if ts < cutoff:
            continue
        if event.type in _REWORK_EVENT_TYPES:
            role = _role_for_task(str(event.task_id or ""), tasks)
            if role:
                row(role)["rework"] += 1
        elif event.type in _RESPAWN_EVENT_TYPES:
            role = _base_role(str(payload(event).get("role")
                                  or payload(event).get("instance_id")
                                  or event.actor or ""))
            if role:
                row(role)["respawn"] += 1

    for task_id, ts in done_at.items():
        if ts < cutoff:
            continue
        role = _role_for_task(task_id, tasks)
        if not role:
            continue
        entry = row(role)
        entry["done"] += 1
        start = dispatched_at.get(task_id)
        if start is not None and ts >= start:
            entry["duration_minutes_total"] += (ts - start).total_seconds() / 60.0
            entry["duration_samples"] += 1

    out = []
    for entry in sorted(rows.values(), key=lambda item: item["role"]):
        samples = entry.pop("duration_samples")
        total = entry.pop("duration_minutes_total")
        entry["avg_duration_minutes"] = round(total / samples, 1) if samples else None
        out.append(entry)
    return out


def _task_done_timestamps(events: EventSlice) -> dict[str, datetime]:
    """First time each task reached done, by event signal."""

    done: dict[str, datetime] = {}
    for _seq, event in events:
        if event.type not in _DONE_EVENT_TYPES:
            continue
        if event.type == "task.status_changed" and str(
                payload(event).get("status") or payload(event).get("to") or "") != "done":
            continue
        task_id = str(event.task_id or payload(event).get("task_id") or "")
        ts = _parse_ts(event.ts)
        if task_id and ts is not None and task_id not in done:
            done[task_id] = ts
    return done


def _oldest_age(tasks: dict[str, Task], status: str, now: datetime) -> int | None:
    ages = []
    for task in tasks.values():
        if task.status != status:
            continue
        started = _parse_ts(task.dispatched_at or task.created_at)
        if started is not None:
            ages.append(max(0, int((now - started).total_seconds())))
    return max(ages) if ages else None


def _role_for_task(task_id: str, tasks: dict[str, Task]) -> str:
    task = tasks.get(task_id)
    if task is None:
        return ""
    owner = ""
    contract = getattr(task, "contract", None)
    if contract is not None:
        owner = str(getattr(contract, "owner_role", "") or "")
    return owner or _base_role(str(task.assigned_to or ""))


def _base_role(instance: str) -> str:
    name = instance.strip()
    if "-" in name and name.rsplit("-", 1)[-1].isdigit():
        return name.rsplit("-", 1)[0]
    return name


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
