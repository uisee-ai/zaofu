"""Share one conservative ledger view when resolving provider hook context."""
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_context_events import ArtifactContextEvents


def hook_context_events(log: EventLog) -> list[ZfEvent]:
    view = getattr(log, '_hook_context_event_view', None)
    if not isinstance(view, ArtifactContextEvents) or view.log is not log:
        view = ArtifactContextEvents(log, preserve_run_aliases=True)
        setattr(log, '_hook_context_event_view', view)
    return view.read_all()
