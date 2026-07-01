"""feishu W2: per-scope debounce queue (doc 99 §4.2).

Accumulates inbound messages for the same scope (scope = chat_id) inside a quiet
window, then flushes them as a single batch. `block(scope)` pauses the timer
while an agent run is active on that scope — pushed messages keep accumulating
but no flush fires until `unblock(scope)`, which arms a fresh quiet window.

This is what makes a chat's rapid messages collapse into one agent turn (no
per-message dispatch jitter) and serializes runs per chat (no two concurrent runs
for the same chat) — directly removing the "two replies merged" class of bug from
doc 99 §3.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

FlushHandler = Callable[[str, list[Any]], None]


class PendingQueue:
    def __init__(self, delay_ms: int, on_flush: FlushHandler) -> None:
        self._delay = max(0.0, delay_ms / 1000.0)
        self._on_flush = on_flush
        self._entries: dict[str, dict[str, Any]] = {}
        self._blocked: set[str] = set()
        self._lock = threading.Lock()

    def push(self, scope: str, msg: Any) -> int:
        """Queue a message for `scope`; (re)arm the debounce timer unless blocked.
        Returns the current queued count for the scope."""
        with self._lock:
            entry = self._entries.get(scope)
            if entry is None:
                entry = {"messages": [], "timer": None}
                self._entries[scope] = entry
            if entry["timer"] is not None:
                entry["timer"].cancel()
                entry["timer"] = None
            entry["messages"].append(msg)
            if scope not in self._blocked:
                entry["timer"] = self._arm(scope)
            return len(entry["messages"])

    def block(self, scope: str) -> None:
        """Pause the debounce timer; pushed messages keep accumulating."""
        with self._lock:
            self._blocked.add(scope)
            entry = self._entries.get(scope)
            if entry is not None and entry["timer"] is not None:
                entry["timer"].cancel()
                entry["timer"] = None

    def unblock(self, scope: str) -> None:
        """Resume the timer; arm a fresh quiet window if anything is queued."""
        with self._lock:
            self._blocked.discard(scope)
            entry = self._entries.get(scope)
            if entry is None or not entry["messages"]:
                return
            if entry["timer"] is not None:
                entry["timer"].cancel()
            entry["timer"] = self._arm(scope)

    def cancel_all(self) -> None:
        with self._lock:
            for entry in self._entries.values():
                if entry["timer"] is not None:
                    entry["timer"].cancel()
            self._entries.clear()
            self._blocked.clear()

    # internal -------------------------------------------------------------

    def _arm(self, scope: str) -> threading.Timer:
        timer = threading.Timer(self._delay, self._flush, args=(scope,))
        timer.daemon = True
        timer.start()
        return timer

    def _flush(self, scope: str) -> None:
        with self._lock:
            entry = self._entries.pop(scope, None)
            batch = list(entry["messages"]) if entry else []
        if batch:
            self._on_flush(scope, batch)
