"""Read-only health projection for the watcher-owned recovery control plane."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text


SCHEMA_VERSION = "control-plane-health.v1"
_RUN_MANAGER_EVENTS = frozenset({
    "run.manager.tick.started",
    "run.manager.tick.completed",
    "run.manager.tick.failed",
    "run.manager.transition",
})
_AUTORESEARCH_PREFIX = "autoresearch."


def build_control_plane_health(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Derive watcher/Run Manager/Autoresearch health from durable facts."""

    state_dir = Path(state_dir)
    now = now or datetime.now(timezone.utc)
    watcher = _watcher_health(state_dir)
    run_manager = _latest_component_event(events, _RUN_MANAGER_EVENTS)
    autoresearch = _latest_component_event_prefix(events, _AUTORESEARCH_PREFIX)
    return {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "components": {
            "watcher": watcher,
            "run_manager": _event_health(run_manager),
            "autoresearch": _event_health(autoresearch),
        },
        "source_refs": {
            "events": "events.jsonl",
            "watcher_guard": "processes/watcher.pid.json",
        },
    }


def write_control_plane_health_projection(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    now: datetime | None = None,
) -> Path:
    """Materialize the projection; it is rebuildable and never workflow truth."""

    state_dir = Path(state_dir)
    path = state_dir / "projections" / "control_plane_health.json"
    payload = build_control_plane_health(state_dir, events, now=now)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def _watcher_health(state_dir: Path) -> dict[str, Any]:
    lock_path = state_dir / "processes" / "watcher.pid.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lock = {}
    owner_pid = _safe_int(lock.get("owner_pid"))
    return {
        "status": "healthy" if owner_pid and _pid_alive(owner_pid) else "missing_or_stale",
        "owner_pid": owner_pid,
        "component": str(lock.get("component") or "watcher"),
        "epoch": str(lock.get("epoch") or ""),
        "started_at": str(lock.get("started_at") or ""),
        "heartbeat_at": str(lock.get("heartbeat_at") or ""),
        "lock_ref": "processes/watcher.pid.json",
    }


def _latest_component_event(
    events: list[ZfEvent],
    event_types: frozenset[str],
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type in event_types:
            return event
    return None


def _latest_component_event_prefix(
    events: list[ZfEvent],
    prefix: str,
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type.startswith(prefix):
            return event
    return None


def _event_health(event: ZfEvent | None) -> dict[str, Any]:
    if event is None:
        return {
            "status": "not_observed",
            "last_event_id": "",
            "last_event_type": "",
            "last_event_ts": "",
        }
    status = "failed" if event.type.endswith(".failed") else "observed"
    return {
        "status": status,
        "last_event_id": event.id,
        "last_event_type": event.type,
        "last_event_ts": event.ts,
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "SCHEMA_VERSION",
    "build_control_plane_health",
    "write_control_plane_health_projection",
]
