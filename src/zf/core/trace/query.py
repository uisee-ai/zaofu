"""Query event traces by correlation, task, or causation chain."""

from __future__ import annotations

from dataclasses import dataclass

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


@dataclass(frozen=True)
class TraceResult:
    trace_id: str
    events: list[ZfEvent]
    mode: str


class TraceQuery:
    def __init__(self, event_log: EventLog) -> None:
        self.event_log = event_log

    def by_correlation_id(self, correlation_id: str) -> TraceResult:
        events = [
            event
            for event in self.event_log.read_all()
            if event.correlation_id == correlation_id
        ]
        return TraceResult(trace_id=correlation_id, events=events, mode="correlation")

    def by_task_id(self, task_id: str) -> TraceResult:
        events = [
            event
            for event in self.event_log.read_all()
            if event.task_id == task_id
        ]
        return TraceResult(trace_id=task_id, events=events, mode="task")

    def causation_chain(self, event_id: str) -> TraceResult:
        events = self.event_log.get_causation_chain(event_id)
        return TraceResult(trace_id=event_id, events=events, mode="causation")

    def show(self, trace_id: str) -> TraceResult:
        """Resolve a trace id flexibly for operator convenience.

        Primary lookup is ``correlation_id``. If that is empty, an event id
        falls back to the event's correlation when present, otherwise to its
        causation chain. Finally, task ids keep legacy trace behavior alive for
        logs written before correlation_id existed.
        """
        correlation = self.by_correlation_id(trace_id)
        if correlation.events:
            return correlation

        all_events = self.event_log.read_all()
        by_id = {event.id: event for event in all_events}
        event = by_id.get(trace_id)
        if event is not None:
            if event.correlation_id:
                return self.by_correlation_id(event.correlation_id)
            return self.causation_chain(trace_id)

        return self.by_task_id(trace_id)
