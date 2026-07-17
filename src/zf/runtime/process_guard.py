"""Single-owner process guard utilities for watcher-like services."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from zf.core.state.atomic_io import atomic_write_text


@dataclass(frozen=True)
class ProcessGuardResult:
    acquired: bool
    lock_path: Path
    owner_pid: int
    reason: str = ""
    component: str = ""
    epoch: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "acquired": self.acquired,
            "lock_path": str(self.lock_path),
            "owner_pid": self.owner_pid,
            "reason": self.reason,
            "component": self.component,
            "epoch": self.epoch,
        }


class SingleOwnerProcessGuard:
    """Small pid-file guard; callers remain responsible for process lifecycle."""

    def __init__(
        self,
        lock_path: Path,
        *,
        owner_pid: int | None = None,
        component: str = "",
    ) -> None:
        self.lock_path = Path(lock_path)
        self.owner_pid = int(owner_pid or os.getpid())
        self.component = str(component or "process")
        self.epoch = uuid4().hex

    def acquire(self) -> ProcessGuardResult:
        current = self._read_lock()
        if current:
            pid = _safe_int(current.get("owner_pid"))
            if pid and _pid_alive(pid):
                return ProcessGuardResult(
                    False,
                    self.lock_path,
                    pid,
                    "owner_alive",
                    str(current.get("component") or ""),
                    str(current.get("epoch") or ""),
                )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock(
            {
                "schema_version": "process-guard.v2",
                "owner_pid": self.owner_pid,
                "component": self.component,
                "epoch": self.epoch,
                "started_at": _utc_now(),
                "heartbeat_at": _utc_now(),
            }
        )
        return ProcessGuardResult(
            True,
            self.lock_path,
            self.owner_pid,
            component=self.component,
            epoch=self.epoch,
        )

    def heartbeat(self) -> bool:
        """Refresh this owner's liveness without stealing a live guard."""

        current = self._read_lock()
        if _safe_int(current.get("owner_pid")) != self.owner_pid:
            return False
        current_epoch = str(current.get("epoch") or "")
        if current_epoch and current_epoch != self.epoch:
            return False
        current.update({
            "schema_version": "process-guard.v2",
            "owner_pid": self.owner_pid,
            "component": self.component,
            "epoch": self.epoch,
            "heartbeat_at": _utc_now(),
        })
        current.setdefault("started_at", _utc_now())
        self._write_lock(current)
        return True

    def release(self) -> bool:
        current = self._read_lock()
        if current and _safe_int(current.get("owner_pid")) not in {0, self.owner_pid}:
            return False
        current_epoch = str(current.get("epoch") or "")
        if current_epoch and current_epoch != self.epoch:
            return False
        try:
            self.lock_path.unlink()
            return True
        except FileNotFoundError:
            return True

    def _read_lock(self) -> dict[str, Any]:
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_lock(self, payload: dict[str, Any]) -> None:
        atomic_write_text(
            self.lock_path,
            json.dumps(payload, sort_keys=True) + "\n",
        )


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["ProcessGuardResult", "SingleOwnerProcessGuard"]
