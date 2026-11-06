"""Replay current resident requests; observations are not request receipts."""

from datetime import datetime, timezone
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.pause_lifecycle import active_request_age_seconds


def resident_request_obligations(
    events: Iterable[ZfEvent], *, now: datetime, timeout_seconds: int,
) -> list[dict[str, Any]]:
    requests: dict[str, dict[str, Any]] = {}
    events = list(events)
    for event in events:
        payload = event.payload or {}
        request_id = str(payload.get("request_id") or payload.get("semantic_triage_request_id") or "")
        if not request_id:
            continue
        if event.type == "run.manager.resident.prompted" and payload.get("prompted"):
            # A resend does not restart the original request's deadline.
            requests.setdefault(request_id, {
                "request_id": request_id, "event_id": event.id,
                "instance_id": str(payload.get("instance_id") or "run-manager"),
                "created_ts": event.ts,
                "expected_output": payload.get("expected_output") or [
                    "run.manager.agent.recommendation",
                ],
                "source_action_checkpoint_id": payload.get("source_action_checkpoint_id", ""),
                "task_id": str(event.task_id or payload.get("task_id") or ""),
                "status": "pending", "response_event_id": "",
            })
        request = requests.get(request_id)
        if (request and event.type in request["expected_output"]
                and event.actor == request["instance_id"]):
            request["status"] = "responded"
            request["response_event_id"] = event.id
    rows = []
    for request in requests.values():
        if request["status"] == "responded":
            continue
        try:
            created = datetime.fromisoformat(str(request["created_ts"]).replace("Z", "+00:00"))
            age = active_request_age_seconds(events, created_at=created, now=now)
        except (TypeError, ValueError):
            age = None
        request["age_seconds"] = age
        request["timeout_seconds"] = timeout_seconds
        request["status"] = "overdue" if age is not None and age >= timeout_seconds else "pending"
        rows.append(request)
    return rows
