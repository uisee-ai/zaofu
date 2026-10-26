"""Reuse only unchanged registry snapshots for high-rate provider activity."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.housekeeping import apply_worker_activity_heartbeat


def _signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except FileNotFoundError:
        return None


def activity_registry(runtime: Any) -> RoleSessionRegistry:
    path = runtime.state_dir / "role_sessions.yaml"
    signature = _signature(path)
    cached = getattr(runtime, "_activity_registry_snapshot", None)
    if cached is not None and cached[0] == signature:
        return cached[1]
    registry = RoleSessionRegistry(path, project_root=str(runtime.project_root))
    # A concurrent replacement must be reloaded next time. Writes still reload
    # under the store lock and invalidate this version before the next lookup.
    runtime._activity_registry_snapshot = (
        (signature, registry) if signature == _signature(path) else None
    )
    return registry


def apply_observed_activity(runtime: Any, event: ZfEvent) -> None:
    apply_worker_activity_heartbeat(activity_registry(runtime), event)
