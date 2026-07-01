"""LH-4.T2: CircuitBreaker — sliding-window failure counter per key.

State machine:
  CLOSED     default; calls proceed normally.
  OPEN       too many failures in the window; calls blocked.
  HALF_OPEN  window expired since last failure; next call is a probe.

Key is typically (role, task_id) or (role, category). Persisted to a
single JSON file so state survives zf restart — crash-safe via atomic
write (temp file + os.replace).
"""

from __future__ import annotations

import json
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-key breaker, persisted.

    Usage:
        br = CircuitBreaker(
            key=("dev", "T1"), max_failures=3, window_seconds=900,
            store_path=state_dir / "circuits.json",
        )
        if not br.can_proceed():
            return  # circuit open; skip dispatch
        try:
            do_the_thing()
        except Exception:
            br.record_failure("reason")
        else:
            br.record_success()
    """

    def __init__(
        self,
        *,
        key: tuple[str, str],
        max_failures: int,
        window_seconds: float,
        store_path: Path,
    ) -> None:
        self.key = key
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.store_path = store_path
        # Test injection point — override with lambda for deterministic tests.
        self._now = time.time

    # ---- public API ----

    def state(self) -> CircuitState:
        """Compute current state from persisted record (lazy read)."""
        rec = self._load_key()
        if not rec.get("failures"):
            return CircuitState.CLOSED
        # Drop failures outside the window.
        cutoff = self._now() - self.window_seconds
        recent = [f for f in rec["failures"] if f >= cutoff]
        if len(recent) >= self.max_failures:
            last_in_window = recent[-1] if recent else 0
            if self._now() - last_in_window < self.window_seconds:
                return CircuitState.OPEN
        if rec.get("tripped_at") is not None and not recent:
            # Previously tripped, window fully expired → probe once.
            return CircuitState.HALF_OPEN
        return CircuitState.CLOSED

    def can_proceed(self) -> bool:
        return self.state() != CircuitState.OPEN

    def record_failure(self, reason: str = "") -> None:
        data = self._load_all()
        k = self._key_str()
        rec = data.get(k, {"failures": [], "tripped_at": None,
                           "last_reason": ""})
        rec["failures"] = list(rec.get("failures") or [])
        rec["failures"].append(self._now())
        rec["last_reason"] = reason
        # Trim failures outside the window so the file doesn't grow.
        cutoff = self._now() - self.window_seconds
        rec["failures"] = [f for f in rec["failures"] if f >= cutoff]
        if len(rec["failures"]) >= self.max_failures:
            rec["tripped_at"] = self._now()
        data[k] = rec
        self._save_all(data)

    def record_success(self) -> None:
        """Close the breaker after a successful call."""
        data = self._load_all()
        k = self._key_str()
        if k in data:
            data[k] = {"failures": [], "tripped_at": None, "last_reason": ""}
            self._save_all(data)

    def reset(self) -> None:
        data = self._load_all()
        data.pop(self._key_str(), None)
        self._save_all(data)

    # ---- internals ----

    def _key_str(self) -> str:
        return f"{self.key[0]}::{self.key[1]}"

    def _load_all(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {}
        try:
            return json.loads(self.store_path.read_text() or "{}")
        except Exception:
            return {}

    def _load_key(self) -> dict[str, Any]:
        return self._load_all().get(
            self._key_str(), {"failures": [], "tripped_at": None},
        )

    def _save_all(self, data: dict[str, Any]) -> None:
        """Atomic write so crash during save doesn't corrupt the file."""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(
            self.store_path.suffix + ".tmp"
        )
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.store_path)
