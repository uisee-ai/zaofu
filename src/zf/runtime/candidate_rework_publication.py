"""Resume a bounded Candidate amendment without publishing it twice."""
import hashlib
from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path


def publish_candidate_rework(service: Any, event_type: str, **kwargs: Any) -> ZfEvent:
    payload = kwargs["payload"]
    checkpoint = payload.get("resume_checkpoint_ref")
    if not checkpoint:
        return service.writer.emit(event_type, **kwargs)
    identity = (event_type, str(checkpoint), str(kwargs.get("correlation_id") or ""))
    lock_id = hashlib.sha256(repr(identity).encode()).hexdigest()
    with locked_path(service.state_dir / "locks" / "candidate-rework-publication" / lock_id):
        for event in service.writer.event_log.read_all():
            if (event.type, event.payload.get("resume_checkpoint_ref"), event.correlation_id or "") != identity:
                continue
            if (event.payload != payload or event.causation_id != kwargs.get("causation_id")
                    or event.actor != kwargs.get("actor") or event.origin in {"worker", "external"}):
                raise ValueError("candidate rework checkpoint publication diverged")
            return event
        return service.writer.emit(event_type, **kwargs)
