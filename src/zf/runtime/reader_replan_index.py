"""Ordered, projection-local lookup for Reader retry accounting."""
from __future__ import annotations

from collections.abc import Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.reader_task_recovery_scope import ReaderTaskRecoveryScopes


class ReaderReplanIndex:
    """Share lookups within one immutable event snapshot, never across ticks."""

    def __init__(self, events: list[ZfEvent]) -> None:
        self._events = events
        self._length = len(events)
        self.by_id: dict[str, ZfEvent] = {}
        self.positions: dict[str, int] = {}
        self._by_type: dict[str, list[tuple[int, ZfEvent]]] = {}
        self._task_scopes: ReaderTaskRecoveryScopes | None = None
        for position, event in enumerate(events):
            self.by_id[event.id] = event
            self.positions[event.id] = position
            self._by_type.setdefault(event.type, []).append((position, event))

    def require_snapshot(self, events: list[ZfEvent]) -> None:
        if events is not self._events or len(events) != self._length:
            raise ValueError("Reader replan index belongs to a different event snapshot")

    def rows(self, event_type: str) -> list[tuple[int, ZfEvent]]:
        return self._by_type.get(event_type, [])

    def ordered(self, event_types: Iterable[str]) -> list[tuple[int, ZfEvent]]:
        return sorted((row for kind in event_types for row in self.rows(kind)),
                      key=lambda row: row[0])

    @property
    def task_scopes(self) -> ReaderTaskRecoveryScopes:
        if self._task_scopes is None:
            self._task_scopes = ReaderTaskRecoveryScopes(self._events)
        return self._task_scopes
