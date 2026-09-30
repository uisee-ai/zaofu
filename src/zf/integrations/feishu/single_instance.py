"""Single-instance guard for the Feishu WS sidecar (feishu P0-3).

Feishu load-balances an app's events across ALL its active WS connections, so a
second (or zombie) connection silently steals events — exactly the failure the
real e2e hit when kill -9 left stale connections.  A provider app must therefore
have one WS owner per host, not one owner per project or workspace.  Project and
workspace locks remain available for backwards-compatible callers, but live
bridge entrypoints acquire the provider lock.

The guard is intentionally local and pure-ish: only touches the lock file and
uses os.kill(pid, 0) for liveness checks.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.workspace.registry import workspace_home

_STALE_SECONDS = 60.0
WS_LOCK_HEARTBEAT_SECONDS = 15.0


@dataclass
class WsLock:
    path: Path

    def release(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self.path.unlink()
        except (OSError, ValueError):
            pass

    def refresh(self) -> None:
        """Refresh the holder timestamp while the bridge process remains live."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("pid") != os.getpid():
            return
        data["ts"] = time.time()
        atomic_write_text(self.path, json.dumps(data))


def refresh_ws_lock_until_stopped(
    lock: WsLock,
    stop: threading.Event,
    interval_seconds: float,
) -> None:
    """Keep the stale-lock TTL meaningful for a long-lived WS process."""
    interval = max(1.0, float(interval_seconds))
    while not stop.wait(interval):
        try:
            lock.refresh()
        except Exception:  # noqa: BLE001 - a failed refresh must not kill WS.
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours to signal


def _lock_path(state_dir, app_id: str) -> Path:
    return (Path(state_dir) / "integrations" / "feishu"
            / f"ws-{app_id or 'default'}.lock")


def workspace_lock_path(workspace: str, app_id: str) -> Path:
    """Stable singleton lock path for one workspace-owned Feishu App WS.

    A project state dir is too narrow: two projects sharing an App would each
    acquire their own lock and Feishu would load-balance inbound events between
    them.  The workspace binding index is the routing authority, so the WS lock
    belongs next to that derived index.
    """
    root = workspace_home()
    safe_workspace = re.sub(r"[^A-Za-z0-9_.-]+", "-", workspace).strip("-") or "default"
    safe_app = re.sub(r"[^A-Za-z0-9_.-]+", "-", app_id).strip("-") or "default"
    return root / "workspaces" / safe_workspace / "integrations" / "feishu" / f"ws-{safe_app}.lock"


def provider_lock_path(app_id: str) -> Path:
    """Stable host-level WS lock path for one Feishu provider App.

    Feishu distributes an App's long-connection events across every active
    connection, regardless of which ZaoFu workspace started it.  The lock thus
    belongs under the workspace home, outside any one project's runtime state.
    """
    safe_app = re.sub(r"[^A-Za-z0-9_.-]+", "-", app_id).strip("-") or "default"
    return workspace_home() / "integrations" / "feishu" / f"ws-{safe_app}.lock"


def acquire_ws_lock(state_dir, app_id: str, *, now: float | None = None,
                    stale_seconds: float = _STALE_SECONDS) -> WsLock | None:
    """Acquire the WS lock, or None if a live holder already exists.

    A held lock is stealable when its holder pid is dead OR its timestamp is
    older than ``stale_seconds`` (a hung holder)."""
    return _acquire_lock(
        _lock_path(state_dir, app_id),
        app_id,
        now=now,
        stale_seconds=stale_seconds,
    )


def acquire_workspace_ws_lock(
    workspace: str,
    app_id: str,
    *,
    now: float | None = None,
    stale_seconds: float = _STALE_SECONDS,
) -> WsLock | None:
    """Acquire the one WS lock for a workspace/App pair."""
    return _acquire_lock(
        workspace_lock_path(workspace, app_id),
        app_id,
        now=now,
        stale_seconds=stale_seconds,
        workspace=workspace,
    )


def acquire_provider_ws_lock(
    app_id: str,
    *,
    now: float | None = None,
    stale_seconds: float = _STALE_SECONDS,
) -> WsLock | None:
    """Acquire the sole host-level WS lock for a Feishu App.

    This is the lock used by ``zf feishu bridge --watch`` and the legacy
    ``zf feishu consume`` route.  It prevents a direct debug bridge, a second
    workspace, or an abandoned sidecar from receiving an arbitrary subset of
    the same App's messages.
    """
    return _acquire_lock(
        provider_lock_path(app_id),
        app_id,
        now=now,
        stale_seconds=stale_seconds,
        scope="provider",
    )


def _acquire_lock(
    path: Path,
    app_id: str,
    *,
    now: float | None,
    stale_seconds: float,
    workspace: str = "",
    scope: str = "",
) -> WsLock | None:
    ts = time.time() if now is None else now
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_path(path):
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            holder = int(existing.get("pid") or 0)
            held_at = float(existing.get("ts") or 0)
            live = (
                holder != os.getpid()
                and _pid_alive(holder)
                and (ts - held_at) < stale_seconds
            )
            if live:
                return None
        payload = {"pid": os.getpid(), "app_id": app_id, "ts": ts}
        if workspace:
            payload["workspace"] = workspace
        if scope:
            payload["scope"] = scope
        atomic_write_text(path, json.dumps(payload))
    return WsLock(path)
