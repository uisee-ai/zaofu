"""Incremental, read-only ownership projection over the active event ledger."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.model import ZfEvent


@dataclass
class _Snapshot:
    version: tuple[int, int, int, int, int]
    offset: int
    assignments: dict[str, str]


def _version(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _fold(assignments: dict[str, str], events: Iterable[ZfEvent]) -> None:
    for event in events:
        payload = event.payload
        if not isinstance(payload, dict):
            continue
        task_id = str(event.task_id or payload.get("task_id") or "")
        if not task_id:
            continue
        if event.type in {"task.dispatched", "fanout.child.dispatched"}:
            assignee = payload.get("assignee") or payload.get("role_instance") or payload.get("role")
            if assignee:
                assignments[task_id] = str(assignee)
        elif event.type in {"fanout.child.completed", "fanout.child.failed", "task.assigned", "task.requeued"}:
            assignments.pop(task_id, None)
        elif event.type == "task.status_changed" and payload.get("to") in {"done", "cancelled", "blocked"}:
            assignments.pop(task_id, None)


def latest_dispatch_assignments(runtime: Any) -> dict[str, str]:
    """Fold only complete new rows; reset on rotation, truncation or replacement."""
    log = runtime.event_log
    path = getattr(log, "path", None)
    try:
        if path is None:
            raise OSError("event log path unavailable")
        for _ in range(2):
            version = _version(path)
            cached = getattr(runtime, "_dispatch_assignment_snapshot", None)
            if isinstance(cached, _Snapshot) and cached.version == version:
                return dict(cached.assignments)
            append_only = (
                isinstance(cached, _Snapshot)
                and cached.version[:2] == version[:2]
                and version[2] > cached.version[2]
                and version[2] >= cached.offset
            )
            offset = cached.offset if append_only else 0
            assignments = dict(cached.assignments) if append_only else {}
            events, consumed = log.read_from_offset(offset)
            after = _version(path)
            if after[:2] != version[:2] or after[2] < consumed:
                runtime._dispatch_assignment_snapshot = None
                continue
            _fold(assignments, events)
            runtime._dispatch_assignment_snapshot = _Snapshot(version, consumed, assignments)
            return dict(assignments)
    except (OSError, AttributeError):
        runtime._dispatch_assignment_snapshot = None
    # Preserve the existing fallback for diagnostic/test event logs without a path.
    assignments = {}
    try:
        _fold(assignments, log.read_days(1))
    except Exception:
        return {}
    return assignments
