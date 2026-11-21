"""Expose mechanical Task admission conflicts to the recovery decision owner."""
from collections.abc import Callable
from typing import Any

from zf.core.events.model import ZfEvent


def task_state_admission_attention_items(
    events: list[ZfEvent], *, make_item: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    # The cancellation is the sole durable diagnosis source. A later reject is
    # only an audit; neither event-window trimming nor its order changes identity.
    return [task_state_admission_attention(event, make_item=make_item) for event in events
            if event.type == 'fanout.cancelled' and event.origin == 'kernel'
            and event.payload.get('failure_scope') == 'task_state_admission']


def task_state_admission_attention(event: ZfEvent, *, make_item: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    payload = event.payload
    item = make_item(
        source="workflow_runtime",
        fingerprint="task_state_admission:" + str(payload.get("plan_admission_incident_id") or event.id),
        severity="medium", title="Task state conflicts with Plan dispatch",
        summary="Completed Tasks require current completion/rework evidence; this is not malformed Plan output.",
        task_id=str(event.task_id or ""), source_event_ids=[event.id],
        suggested_route="run_manager", failure_class="task_fanout_rejected",
        owner_route="run_manager", action_policy="needs_diagnosis", intervention_class="diagnose",
    )
    item.update({key: payload.get(key, "") for key in
                 ("workflow_run_id", "stage_id", "failure_scope", "plan_admission_incident_id")})
    return item
