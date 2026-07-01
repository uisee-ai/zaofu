"""Single-instance guard for the Feishu WS sidecar (feishu P0-3).

Feishu load-balances an app's events across ALL its active WS connections, so a
second (or zombie) connection silently steals events — exactly the failure the
real e2e hit when kill -9 left stale connections. This lock makes the WS sidecar
single-instance per (state_dir, app): a second start is refused while a live
holder exists; a stale lock (holder dead, or older than the TTL) can be stolen.

The guard is intentionally local and pure-ish: only touches the lock file and
uses os.kill(pid, 0) for liveness checks.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

_STALE_SECONDS = 60.0


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


def acquire_ws_lock(state_dir, app_id: str, *, now: float | None = None,
                    stale_seconds: float = _STALE_SECONDS) -> WsLock | None:
    """Acquire the WS lock, or None if a live holder already exists.

    A held lock is stealable when its holder pid is dead OR its timestamp is
    older than ``stale_seconds`` (a hung holder)."""
    ts = time.time() if now is None else now
    path = _lock_path(state_dir, app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if isinstance(existing, dict):
        holder = int(existing.get("pid") or 0)
        held_at = float(existing.get("ts") or 0)
        live = (holder != os.getpid() and _pid_alive(holder)
                and (ts - held_at) < stale_seconds)
        if live:
            return None  # a live, fresh holder owns it
    path.write_text(
        json.dumps({"pid": os.getpid(), "app_id": app_id, "ts": ts}),
        encoding="utf-8")
    return WsLock(path)
