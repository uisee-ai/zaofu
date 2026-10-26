"""Wake the runtime once per immutable watcher snapshot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


class _RateLimiter(Protocol):
    def allow(self, event_type: str) -> bool: ...


class _Orchestrator(Protocol):
    def run_once(self, *, events: list[ZfEvent], consumed_offset: int) -> object: ...


def process_watched_batch(
    events: list[ZfEvent],
    *,
    consumed_offset: int,
    wake_patterns: set[str],
    wake_worthy_fn: Callable[[ZfEvent], bool],
    rate_limiter: _RateLimiter,
    orchestrator: _Orchestrator,
    simulation: bool,
    event_log: EventLog,
    stop_watcher: Callable[[], None],
) -> bool:
    """Process all facts in order if any member wakes the runtime.

    Observation-only snapshots remain pending for durable idle catch-up.
    Never acknowledge beyond the captured boundary: reactions may append facts
    that must be consumed on the next poll or after restart.
    """
    should_wake = False
    for event in events:
        terminal = simulation and event.type in {
            "run.goal.completed", "run.goal.blocked",
        }
        if terminal or (
            event.type in wake_patterns
            and wake_worthy_fn(event)
            and rate_limiter.allow(event.type)
        ):
            should_wake = True
    if not should_wake:
        return False

    orchestrator.run_once(events=events, consumed_offset=consumed_offset)
    if simulation:
        from zf.core.events.writer import EventWriter
        from zf.runtime.simulation_lifecycle import emit_simulation_done

        for event in events:
            if event.type not in {"run.goal.completed", "run.goal.blocked"}:
                continue
            done = emit_simulation_done(
                event,
                events=event_log.read_all(),
                writer=EventWriter(event_log, default_origin="kernel"),
            )
            if done is not None:
                stop_watcher()
    return True
