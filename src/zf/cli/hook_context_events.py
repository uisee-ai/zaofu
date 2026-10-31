"""Share one conservative ledger view when resolving provider hook context."""
import re

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_context_events import ArtifactContextEvents


def hook_context_events(log: EventLog) -> list[ZfEvent]:
    view = getattr(log, '_hook_context_event_view', None)
    if not isinstance(view, ArtifactContextEvents) or view.log is not log:
        view = ArtifactContextEvents(log, preserve_run_aliases=True)
        setattr(log, '_hook_context_event_view', view)
    return view.read_all()


class HookEventWriter(EventWriter):
    """Resolve hook parents without retaining unrelated telemetry bodies."""

    def _event_by_id(self, event_id: str) -> ZfEvent | None:
        index = getattr(self.event_log, 'index', None)
        cached = index.lookup_event(event_id) if index is not None else None
        if cached is not None:
            return cached
        # Other spellings can be JSON-escaped; preserve the general full read.
        if not re.fullmatch(r'[A-Za-z0-9_-]+', event_id):
            return super()._event_by_id(event_id)
        events = ArtifactContextEvents(
            self.event_log, preserve_event_ids=(event_id,),
        ).read_all()
        return next((event for event in reversed(events) if event.id == event_id), None)
