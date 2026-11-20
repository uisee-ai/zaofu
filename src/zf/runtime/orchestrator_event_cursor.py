"""Durable EventLog cursor ownership for the orchestrator."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.events.offset import EventOffset


class EventCursorMixin:
    """Persist only EventLog boundaries that the runtime has consumed."""

    def _read_reaction_events(
        self, pushed: list[ZfEvent] | None, consumed_offset: int | None,
    ) -> tuple[list[ZfEvent], int | None]:
        if pushed is None:
            return self.event_log.read_from_offset(self._load_offset())
        if consumed_offset is None:
            return pushed, None
        if consumed_offset < 0:
            raise ValueError("consumed event offset must be non-negative")
        # Include pending observation-only batches, but leave callback appends
        # after this immutable watcher boundary for the next reaction.
        return self.event_log.read_from_offset(
            self._load_offset(), through=consumed_offset,
        )

    def _load_offset(self) -> int:
        try:
            session = self.session_store.load()
            identity = tuple(session.latest_event_identity) or None
            return EventOffset(session.latest_event_offset, identity)
        except Exception:
            return 0

    def _persist_offset(self, offset: int) -> None:
        try:
            self.session_store.update(latest_event_offset=offset)
        except Exception:
            pass

    def acknowledge_consumed_offset(self, consumed_offset: int) -> None:
        """Persist an EventWatcher boundary after its batch was fully handled."""
        if consumed_offset < 0:
            raise ValueError("consumed event offset must be non-negative")
        previous = self._load_offset()
        if (consumed_offset > previous or
                getattr(consumed_offset, "identity", None) != getattr(previous, "identity", None)):
            self._persist_offset(consumed_offset)


__all__ = ["EventCursorMixin"]
