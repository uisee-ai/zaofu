"""Build callbacks for the foreground runtime watcher."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.cli.watched_batch import process_watched_batch
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
        process_watched_batch(
            events, consumed_offset=offset, orchestrator=orchestrator,
            wake_patterns=wake_patterns, wake_worthy_fn=wake_worthy_fn,
            rate_limiter=rate_limiter, simulation=simulation,
            event_log=event_log, stop_watcher=stop_watcher,
        )

    return on_event, on_batch
