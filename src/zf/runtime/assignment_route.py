"""Read-only assignment-to-execution route projection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj


ASSIGNMENT_ROUTE_EVENTS = {
    "assignment.intent.proposed",
    "channel.synthesis.proposed",
    "workflow.invoke.requested",
    "workflow.invoke.accepted",
    "workflow.invoke.rejected",
    "task.dispatched",
}


def project_assignment_routes(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project assignment intent, squad synthesis, and workflow invoke stages."""

    state_dir = Path(state_dir)
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    now = now or datetime.now(timezone.utc)
    indexed = list(enumerate(events, start=1))
    by_task: dict[str, list[tuple[int, ZfEvent]]] = defaultdict(list)
    unlinked_syntheses: dict[str, tuple[int, ZfEvent]] = {}

    for seq, event in indexed:
        if event.type not in ASSIGNMENT_ROUTE_EVENTS:
            continue
        task_id = _event_task(event)
        if task_id:
            by_task[task_id].append((seq, event))
        elif event.type == "channel.synthesis.proposed":
            unlinked_syntheses[event.id] = (seq, event)

    for seq, event in indexed:
        if event.type != "workflow.invoke.requested":
            continue
        task_id = _event_task(event)
        synthesis_event_id = _payload_str(_payload(event), "synthesis_event_id")
        if not task_id or not synthesis_event_id:
            continue
        synthesis = unlinked_syntheses.get(synthesis_event_id)
        if synthesis and synthesis not in by_task[task_id]:
            by_task[task_id].append(synthesis)

    linked_synthesis_ids = {
        _payload_str(_payload(event), "synthesis_event_id")
        for _, events_for_task in by_task.items()
        for _, event in events_for_task
        if event.type == "workflow.invoke.requested"
    }
    channel_routes = [
        _project_route(
            f"_channel:{_payload_str(_payload(event), 'channel_id')}:{_payload_str(_payload(event), 'thread_id') or 'main'}",
            [(seq, event)],
        )
        for event_id, (seq, event) in unlinked_syntheses.items()
        if event_id not in linked_synthesis_ids
    ]
    routes = [
        _project_route(task_id, sorted(events_for_task, key=lambda item: item[0]))
        for task_id, events_for_task in sorted(by_task.items())
    ]
    routes.extend(channel_routes)
    routes.sort(key=lambda row: int(row.get("last_seq") or 0), reverse=True)
    return redact_obj({
        "schema_version": "assignment-routes.v1",
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "summary": {
            "routes": len(routes),
            "intent_only": sum(1 for row in routes if row.get("stage") == "assignment_intent"),
            "workflow_requested": sum(1 for row in routes if row.get("stage") == "workflow_requested"),
            "execution_accepted": sum(1 for row in routes if row.get("stage") == "execution_accepted"),
            "executing": sum(1 for row in routes if row.get("stage") == "executing"),
        },
        "routes": routes[:200],
    })


def _project_route(route_key: str, indexed_events: list[tuple[int, ZfEvent]]) -> dict[str, Any]:
    indexed_events = sorted(indexed_events, key=lambda item: item[0])
    event_types = {event.type for _, event in indexed_events}
    latest = indexed_events[-1][1]
    assignment = _last_event(indexed_events, "assignment.intent.proposed")
    synthesis = _last_event(indexed_events, "channel.synthesis.proposed")
    workflow_requested = _last_event(indexed_events, "workflow.invoke.requested")
    workflow_accepted = _last_event(indexed_events, "workflow.invoke.accepted")
    workflow_rejected = _last_event(indexed_events, "workflow.invoke.rejected")
    dispatched = _last_event(indexed_events, "task.dispatched")
    task_id = _event_task(latest) or (route_key if not route_key.startswith("_channel:") else "")
    assignment_payload = _payload(assignment)
    synthesis_payload = _payload(synthesis)
    workflow_payload = _payload(workflow_requested or workflow_accepted or workflow_rejected)
    stage = _route_stage(event_types)
    return {
        "route_id": task_id or route_key,
        "task_id": task_id,
        "stage": stage,
        "stage_label": _stage_label(stage),
        "assignee_type": _payload_str(assignment_payload, "assignee_type") or (
            "squad" if _payload_str(synthesis_payload, "channel_id") else ""
        ),
        "assignee_id": _payload_str(assignment_payload, "assignee_id"),
        "assignee_label": _payload_str(assignment_payload, "assignee_label"),
        "role": _payload_str(assignment_payload, "role"),
        "backend": _payload_str(assignment_payload, "backend"),
        "channel_id": (
            _payload_str(assignment_payload, "channel_id")
            or _payload_str(synthesis_payload, "channel_id")
            or _payload_str(workflow_payload, "channel_id")
        ),
        "thread_id": (
            _payload_str(synthesis_payload, "thread_id")
            or _payload_str(workflow_payload, "thread_id")
        ),
        "pattern_id": _payload_str(workflow_payload, "pattern_id")
        or _payload_str(_as_record(synthesis_payload.get("recommended_workflow")), "pattern_id"),
        "dispatches": bool(assignment_payload.get("dispatches")) if assignment_payload else False,
        "execution_started": dispatched is not None,
        "workflow_request_event_id": workflow_requested.id if workflow_requested else "",
        "synthesis_event_id": synthesis.id if synthesis else _payload_str(workflow_payload, "synthesis_event_id"),
        "last_event_id": latest.id,
        "last_event_type": latest.type,
        "last_event_at": latest.ts,
        "last_seq": indexed_events[-1][0],
        "summary": _route_summary(indexed_events),
        "evidence_event_ids": [event.id for _, event in indexed_events if event.id],
        "events": [_event_ref(seq, event) for seq, event in indexed_events],
    }


def _route_stage(event_types: set[str]) -> str:
    if "task.dispatched" in event_types:
        return "executing"
    if "workflow.invoke.accepted" in event_types:
        return "execution_accepted"
    if "workflow.invoke.rejected" in event_types:
        return "workflow_rejected"
    if "workflow.invoke.requested" in event_types:
        return "workflow_requested"
    if "channel.synthesis.proposed" in event_types:
        return "squad_synthesis"
    if "assignment.intent.proposed" in event_types:
        return "assignment_intent"
    return "observed"


def _stage_label(stage: str) -> str:
    return {
        "assignment_intent": "Assignment Intent Only",
        "squad_synthesis": "Squad Synthesis Proposed",
        "workflow_requested": "Workflow Invoke Requested",
        "workflow_rejected": "Workflow Invoke Rejected",
        "execution_accepted": "Execution Accepted",
        "executing": "Task Dispatched",
    }.get(stage, "Observed")


def _route_summary(indexed_events: list[tuple[int, ZfEvent]]) -> str:
    labels = []
    for event_type, label in (
        ("assignment.intent.proposed", "assignment intent"),
        ("channel.synthesis.proposed", "squad synthesis"),
        ("workflow.invoke.requested", "workflow requested"),
        ("workflow.invoke.accepted", "execution accepted"),
        ("workflow.invoke.rejected", "workflow rejected"),
        ("task.dispatched", "task dispatched"),
    ):
        if any(event.type == event_type for _, event in indexed_events):
            labels.append(label)
    return " -> ".join(labels)


def _last_event(indexed_events: list[tuple[int, ZfEvent]], event_type: str) -> ZfEvent | None:
    return next((event for _, event in reversed(indexed_events) if event.type == event_type), None)


def _event_ref(seq: int, event: ZfEvent) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_id": event.id,
        "type": event.type,
        "task_id": _event_task(event),
        "actor": event.actor or "",
        "ts": event.ts,
    }


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None or not isinstance(event.payload, dict):
        return {}
    return event.payload


def _payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if value is not None else ""


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _event_task(event: ZfEvent | None) -> str:
    payload = _payload(event)
    return str(
        (event.task_id if event is not None else "")
        or payload.get("task_id")
        or payload.get("current_task_id")
        or payload.get("active_task")
        or ""
    )
