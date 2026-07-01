"""Event projector runner for append-side rebuildable projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


ProjectorHandler = Callable[[EventLog, ZfEvent], None]
ProjectorFilter = Callable[[ZfEvent], bool]


@dataclass(frozen=True)
class ProjectorResult:
    name: str
    event_type: str
    status: str
    error_type: str = ""
    error: str = ""


@dataclass(frozen=True)
class EventProjector:
    name: str
    handler: ProjectorHandler
    event_filter: ProjectorFilter | None = None

    def should_project(self, event: ZfEvent) -> bool:
        if self.event_filter is None:
            return True
        return self.event_filter(event)

    def project(self, event_log: EventLog, event: ZfEvent) -> None:
        self.handler(event_log, event)


class ProjectorRunner:
    """Runs registered append-side projectors without blocking event append."""

    def __init__(self, projectors: tuple[EventProjector, ...] = ()) -> None:
        self.projectors = tuple(projectors)

    def run(self, event_log: EventLog, event: ZfEvent) -> list[ProjectorResult]:
        results: list[ProjectorResult] = []
        for projector in self.projectors:
            if not projector.should_project(event):
                results.append(ProjectorResult(
                    name=projector.name,
                    event_type=event.type,
                    status="skipped",
                ))
                continue
            try:
                projector.project(event_log, event)
            except Exception as exc:
                results.append(ProjectorResult(
                    name=projector.name,
                    event_type=event.type,
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                ))
            else:
                results.append(ProjectorResult(
                    name=projector.name,
                    event_type=event.type,
                    status="ok",
                ))
        return results
