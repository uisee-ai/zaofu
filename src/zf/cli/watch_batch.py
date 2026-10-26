"""Build callbacks for the foreground runtime watcher."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.simulation_lifecycle import SIMULATION_TERMINAL_EVENTS, emit_simulation_done
from zf.runtime.wake_patterns import WakeRateLimiter

if TYPE_CHECKING:
    from zf.runtime.workflow_runtime import WorkflowRuntimeCoordinator


def make_watch_callbacks(
    *,
    orchestrator: WorkflowRuntimeCoordinator,
    wake_patterns: set[str],
    wake_worthy_fn: Callable[[ZfEvent], bool],
    rate_limiter: WakeRateLimiter,
    simulation: bool,
    event_log: EventLog,
    stop_watcher: Callable[[], None],
) -> tuple[Callable[[str], None], Callable[[list[ZfEvent], int], None]]:
    """Schedule once per snapshot, preserving its order and exact cursor."""

    def on_event(_line: str) -> None:
        # EventWatcher still applies quiesce/resume for each event. Runtime
        # dispatch waits for the complete immutable snapshot below.
        pass

    def on_batch(events: list[ZfEvent], offset: int) -> None:
        should_wake = False
        for event in events:
            if simulation and event.type in SIMULATION_TERMINAL_EVENTS:
                should_wake = True
            elif (
                event.type in wake_patterns
                and wake_worthy_fn(event)
                and rate_limiter.allow(event.type)
            ):
                should_wake = True
        if not should_wake:
            # Pure observations and rate-limited wakes retain the existing
            # periodic durable catch-up path; do not acknowledge them here.
            return
        orchestrator.run_once(events=events, consumed_offset=offset)
        if simulation:
            for event in events:
                if event.type not in SIMULATION_TERMINAL_EVENTS:
                    continue
                done = emit_simulation_done(
                    event, events=event_log.read_all(),
                    writer=EventWriter(event_log, default_origin="kernel"),
                )
                if done is not None:
                    stop_watcher()

    return on_event, on_batch
