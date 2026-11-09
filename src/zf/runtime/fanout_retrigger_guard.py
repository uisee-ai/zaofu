"""Suppress recovery fanout triggers for an already verified generation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import (
    CANDIDATE_FREEZE_RECEIPT_SCHEMA,
    latest_task_pipeline_generation_context,
)
from zf.runtime.writer_fanout_generation import completed_writer_generation


def suppress_handled_stall_trigger(
    runtime: Any, *, trigger_event: ZfEvent, stage_id: str,
) -> bool:
    """Coalesce a structural recovery with its exact original trigger."""
    events = runtime.event_log.read_all()
    by_id = {event.id: event for event in events}
    original_id = str(trigger_event.payload.get("original_trigger_event_id") or "")
    original = (
        by_id.get(original_id)
        if trigger_event.actor == "zf-stall-redispatch"
        else trigger_event
    )
    if original is None or original.actor == "zf-stall-redispatch":
        return False

    def is_recovery(event: ZfEvent) -> bool:
        body = dict(event.payload)
        if (event.actor != "zf-stall-redispatch" or event.type != original.type
                or event.correlation_id != original.correlation_id
                or event.task_id != original.task_id
                or body.get("original_trigger_event_id") != original.id):
            return False
        for key in ("original_trigger_event_id", "redispatch_attempt", "redispatch_fingerprint"):
            body.pop(key, None)
        # The detector may fill the configured route for a legacy trigger.
        if "flow_kind" not in original.payload:
            body.pop("flow_kind", None)
        return body == original.payload

    if trigger_event is not original and not is_recovery(trigger_event):
        return False
    family = {original.id, *(event.id for event in events if is_recovery(event))}
    handled = next((event for event in events if (
        event.type == "fanout.started" and event.payload.get("stage_id") == stage_id
        and event.payload.get("trigger_event_id") in family - {trigger_event.id}
    )), None)
    if handled is None:
        return False
    reason = "stall_trigger_already_handled"
    if not any(event.type == "fanout.retrigger.suppressed"
               and event.payload.get("stage_id") == stage_id
               and event.payload.get("trigger_event_id") == trigger_event.id
               and event.payload.get("reason") == reason for event in events):
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed", actor="zf-cli", origin="kernel",
            payload={"stage_id": stage_id, "trigger_event_id": trigger_event.id,
                     "original_trigger_event_id": original.id, "reason": reason,
                     "handled_trigger_event_id": handled.payload["trigger_event_id"],
                     "handled_fanout_id": handled.payload.get("fanout_id", "")},
            causation_id=trigger_event.id, correlation_id=trigger_event.correlation_id,
        ))
    return True


def suppress_stale_task_pipeline_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    stage_ids: Sequence[str],
    correlation_id: str,
) -> bool:
    """Reject replay of a frozen Candidate from an older v4 generation."""

    payload = (
        trigger_event.payload
        if isinstance(trigger_event.payload, dict)
        else {}
    )
    if (
        trigger_event.type != "candidate.ready"
        or str(payload.get("schema_version") or "")
        != CANDIDATE_FREEZE_RECEIPT_SCHEMA
    ):
        return False
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or trigger_event.correlation_id
        or ""
    ).strip()
    claimed_generation_id = str(payload.get("generation_id") or "").strip()
    if not workflow_run_id or not claimed_generation_id:
        return False
    try:
        events = runtime.event_log.read_all()
    except Exception:
        return False
    current = latest_task_pipeline_generation_context(
        events,
        workflow_run_id=workflow_run_id,
    )
    current_generation_id = str(
        (current or {}).get("generation_id") or ""
    ).strip()
    if (
        not current_generation_id
        or current_generation_id == claimed_generation_id
    ):
        return False
    already_suppressed = any(
        event.type == "fanout.retrigger.suppressed"
        and isinstance(event.payload, dict)
        and event.payload.get("trigger_event_id") == trigger_event.id
        and event.payload.get("reason")
        == "candidate_ready_stale_task_pipeline_generation"
        for event in events
    )
    if not already_suppressed:
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed",
            actor="zf-cli",
            origin="kernel",
            payload={
                "stage_ids": sorted({str(item) for item in stage_ids if item}),
                "trigger_event_id": trigger_event.id,
                "reason": "candidate_ready_stale_task_pipeline_generation",
                "workflow_run_id": workflow_run_id,
                "claimed_generation_id": claimed_generation_id,
                "current_generation_id": current_generation_id,
                "current_generation_admitted_event_id": str(
                    (current or {}).get("generation_admitted_event_id") or ""
                ),
                "task_map_generation": str(
                    payload.get("task_map_generation") or ""
                ),
            },
            causation_id=trigger_event.id,
            correlation_id=correlation_id,
        ))
    return True


def suppress_completed_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    stage_id: str,
    task_ids: Sequence[str],
    task_map_generation: str,
    workflow_run_id: str,
    correlation_id: str,
) -> bool:
    try:
        events = runtime.event_log.read_all()
    except Exception:
        events = []
    completed = completed_writer_generation(
        events,
        trigger_event=trigger_event,
        task_ids=task_ids,
        task_map_generation=task_map_generation,
        workflow_run_id=workflow_run_id,
    )
    if completed is None:
        return False
    already_suppressed = any(
        event.type == "fanout.retrigger.suppressed"
        and isinstance(event.payload, dict)
        and event.payload.get("stage_id") == stage_id
        and event.payload.get("trigger_event_id") == trigger_event.id
        and event.payload.get("reason") == "generation_already_verified"
        for event in events
    )
    if not already_suppressed:
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed",
            actor="zf-cli",
            payload={
                "stage_id": stage_id,
                "trigger_event_id": trigger_event.id,
                "reason": "generation_already_verified",
                "task_ids": completed.task_ids,
                "task_map_generation": task_map_generation,
                "candidate_event_id": completed.candidate_event_id,
                "candidate_head_commit": completed.candidate_head_commit,
                "candidate_ref": completed.candidate_ref,
                "verification_event_id": completed.verification_event_id,
            },
            causation_id=trigger_event.id,
            correlation_id=correlation_id,
        ))
    return True
