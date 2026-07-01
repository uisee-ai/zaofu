"""Single-owner process guard utilities for watcher-like services."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text


@dataclass(frozen=True)
class ProcessGuardResult:
    acquired: bool
    lock_path: Path
    owner_pid: int
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "acquired": self.acquired,
            "lock_path": str(self.lock_path),
            "owner_pid": self.owner_pid,
            "reason": self.reason,
        }


class SingleOwnerProcessGuard:
    """Small pid-file guard; callers remain responsible for process lifecycle."""

    def __init__(self, lock_path: Path, *, owner_pid: int | None = None) -> None:
        self.lock_path = Path(lock_path)
        self.owner_pid = int(owner_pid or os.getpid())

    def acquire(self) -> ProcessGuardResult:
        current = self._read_lock()
        if current:
            pid = _safe_int(current.get("owner_pid"))
            if pid and _pid_alive(pid):
                return ProcessGuardResult(False, self.lock_path, pid, "owner_alive")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.lock_path,
            json.dumps({"owner_pid": self.owner_pid}, sort_keys=True) + "\n",
        )
        return ProcessGuardResult(True, self.lock_path, self.owner_pid)

    def release(self) -> bool:
        current = self._read_lock()
        if current and _safe_int(current.get("owner_pid")) not in {0, self.owner_pid}:
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


__all__ = ["ProcessGuardResult", "SingleOwnerProcessGuard"]
