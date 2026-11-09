"""Evidence-bound redrive of task recovery scans cancelled by another task."""

from zf.core.events.model import ZfEvent


def _payload(event: ZfEvent) -> dict:
    return event.payload if isinstance(event.payload, dict) else {}


def cross_task_replan_cancellation(
    events: list[ZfEvent], task_id: str, failed_event_id: str, *, request_id: str = "",
) -> str:
    requests = {
        event.id: event for event in events
        if _payload(event).get("schema_version") == "semantic-replan-request.v1"
    }
    starts = {
        str(_payload(event).get("fanout_id") or ""): event
        for event in events if event.type == "fanout.started"
    }
    own = [
        event for event in requests.values()
        if _payload(event).get("task_id") == task_id
        and _payload(event).get("rework_of") == failed_event_id
        and _payload(event).get("handoff_kind") == "task_base_recovery"
        and (not request_id or _payload(event).get("request_id") == request_id)
    ]
    if not own:
        return ""
    request = own[-1]
    for event in reversed(events):
        payload = _payload(event)
        if event.type != "fanout.cancelled" or payload.get("reason") != "superseded_by_latest_fanout":
            continue
        old = starts.get(str(payload.get("fanout_id") or ""))
        new = starts.get(str(payload.get("superseded_by") or ""))
        if old is None or new is None or _payload(old).get("trigger_event_id") != request.id:
            continue
        newer_request = requests.get(str(_payload(new).get("trigger_event_id") or ""))
        newer = _payload(newer_request) if newer_request else {}
        if (_payload(request).get("workflow_run_id")
                and newer.get("handoff_kind") == "task_base_recovery"
                and newer.get("task_id") and newer.get("task_id") != task_id
                and newer.get("workflow_run_id") == _payload(request).get("workflow_run_id")):
            return event.id
    return ""
