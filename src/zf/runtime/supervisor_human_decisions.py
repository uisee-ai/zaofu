"""Match applied owner dismissals to their exact escalation scope."""
from __future__ import annotations

from zf.core.events import ZfEvent


def applied_human_decision_ref(item: dict, events_by_id: dict, decisions: dict) -> dict:
    source_ids = [str(value) for value in item.get("source_event_ids") or [] if value]
    source_events = [events_by_id.get(source_id) for source_id in source_ids]
    if not source_events or not all(
        event is not None and event.type == "human.escalate" for event in source_events
    ):
        return {}
    matched = [decisions.get(source_id) for source_id in source_ids]
    if not all(matched):
        return {}
    return {"event_id": matched[-1].id, "resolution": "human_decision_applied"}


def applied_human_decisions(events: list[ZfEvent]) -> dict[str, ZfEvent]:
    event_positions = {event.id: index for index, event in enumerate(events) if event.id}
    escalations = {
        event.id: event
        for event in events
        if event.id and event.type == "human.escalate"
    }
    decisions: dict[str, ZfEvent] = {}
    for event in events:
        if event.type != "run.manager.human_decision.applied":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("decision") or "") != "dismissed"
            or str(payload.get("next_route") or "") != "none"
        ):
            continue
        source_id = str(payload.get("source_escalation_event_id") or "").strip()
        source = escalations.get(source_id)
        if (
            source is None
            or event_positions.get(source_id, len(events)) >= event_positions.get(event.id, -1)
            or not _human_decision_matches_escalation(event, payload, source)
        ):
            continue
        decisions[source_id] = event
    return decisions


def _human_decision_matches_escalation(
    decision_event: ZfEvent,
    decision_payload: dict[str, Any],
    escalation: ZfEvent,
) -> bool:
    escalation_payload = escalation.payload if isinstance(escalation.payload, dict) else {}
    escalation_token = str(
        escalation_payload.get("decision_token") or escalation_payload.get("approval_ref") or ""
    ).strip()
    decision_token = str(decision_payload.get("decision_token") or "").strip()
    if escalation_token and decision_token != escalation_token:
        return False
    escalation_task = str(escalation.task_id or escalation_payload.get("task_id") or "").strip()
    decision_task = str(decision_event.task_id or decision_payload.get("task_id") or "").strip()
    if escalation_task and decision_task != escalation_task:
        return False
    escalation_run = str(
        escalation_payload.get("workflow_run_id")
        or escalation_payload.get("run_id")
        or escalation.correlation_id
        or ""
    ).strip()
    decision_run = str(
        decision_payload.get("workflow_run_id")
        or decision_payload.get("run_id")
        or decision_event.correlation_id
        or ""
    ).strip()
    return not escalation_run or decision_run == escalation_run


