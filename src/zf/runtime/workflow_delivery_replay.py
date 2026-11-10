"""Idempotent replay results for already-submitted workflow requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.light_flow import light_flow_metadata
from zf.runtime.run_admission import request_admission_view


def submitted_request_replay_result(
    *,
    config: Any,
    state_dir: Path,
    projection: dict[str, Any],
    events: list[ZfEvent],
) -> dict[str, Any]:
    """Return a replay result without emitting a second workflow run."""

    request_id = str(projection.get("request_id") or "")
    run_id = str(projection.get("run_id") or request_id)
    admission = request_admission_view(
        events,
        request_id=request_id,
        run_id=run_id,
    )
    if bool(admission.get("terminal")):
        terminal_type = str(admission.get("terminal_type") or "run terminal")
        return {
            "schema_version": "workflow.submit.apply.v1",
            "status": "STOP",
            "dry_run": False,
            "event_type": "workflow.submit.rejected",
            "payload": {
                "request_id": request_id,
                "run_id": run_id,
                "kind": str(projection.get("kind") or ""),
            },
            "request": projection,
            "idempotent_replay": True,
            "workflow_invoke_event_id": "",
            "workflow_entry_event_id": "",
            "workflow_invoke_status": "terminal",
            "next_action": (
                "the prior Run is terminal; use controlled recovery/replan "
                "instead of replaying workflow-start"
            ),
            "event_ids": [],
            "state_dir": str(state_dir),
            "blockers": [{
                "severity": "STOP",
                "kind": "workflow_request_run_terminal",
                "title": "workflow request 的 Run 已终止",
                "message": (
                    f"Run ended with {terminal_type}; workflow-start cannot "
                    "silently replay it."
                ),
                "fix_it": (
                    "Use the controlled recovery/replan path, or create a new "
                    "Task/Workflow Request when a fresh delivery is intended."
                ),
                "safe_auto_fix": False,
            }],
        }

    light_metadata = light_flow_metadata(
        config,
        flow_kind=str(projection.get("kind") or ""),
    )
    entry_trigger = (
        str(light_metadata.get("light_entry_trigger") or "prd.requested")
        if light_metadata is not None else ""
    )
    related_types = {"workflow.submit.accepted", "workflow.invoke.requested"}
    if entry_trigger:
        related_types.add(entry_trigger)
    related = [
        event for event in events
        if str(event.correlation_id or "") == request_id
        and event.type in related_types
    ]
    invoked = next(
        (event for event in reversed(related) if event.type == "workflow.invoke.requested"),
        None,
    )
    light_entry = next(
        (event for event in reversed(related) if event.type == entry_trigger),
        None,
    )
    if invoked is not None:
        invoke_status = "already_requested"
        next_action = "workflow request is already running"
    elif light_entry is not None:
        invoke_status = "already_requested"
        next_action = "light topology entry is already running"
    else:
        invoke_status = "already_submitted"
        next_action = (
            "inspect/resume the submitted request; duplicate ignition was suppressed"
        )
    return {
        "schema_version": "workflow.submit.apply.v1",
        "status": "accepted",
        "dry_run": False,
        "event_type": "workflow.submit.accepted",
        "payload": {
            "request_id": request_id,
            "run_id": run_id,
            "kind": str(projection.get("kind") or ""),
        },
        "request": projection,
        "idempotent_replay": True,
        "workflow_invoke_event_id": str(invoked.id if invoked is not None else ""),
        "workflow_entry_event_id": str(light_entry.id if light_entry is not None else ""),
        "workflow_invoke_status": invoke_status,
        "next_action": next_action,
        "event_ids": [event.id for event in related],
        "state_dir": str(state_dir),
        "blockers": [],
    }


__all__ = ["submitted_request_replay_result"]


def workflow_invoke_visibility(
    events: list[ZfEvent],
    *,
    source_event_id: str,
) -> dict[str, str]:
    for event in reversed(events):
        if event.type not in {
            "workflow.invoke.accepted",
            "workflow.invoke.rejected",
            "run.admission.queued",
            "run.admission.admitted",
            "run.admission.rejected",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("source_event_id") or "") != source_event_id:
            continue
        if event.type == "workflow.invoke.accepted":
            return {
                "status": "accepted",
                "next_action": "watch fanout/task events; workflow invoke was consumed by the orchestrator",
            }
        if event.type == "run.admission.queued":
            return {
                "status": "queued",
                "next_action": "the request is queued behind the active Project Run",
            }
        if event.type == "run.admission.admitted":
            return {
                "status": "admitted",
                "next_action": "the Run is admitted; wait for the invoke consumer outcome",
            }
        return {
            "status": "rejected",
            "next_action": "inspect workflow.invoke.rejected reason and resubmit after correction",
        }
    return {
        "status": "pending_consumer",
        "next_action": "ensure `zf start` watcher is running so workflow.invoke.requested is consumed",
    }
