"""Exact failed Reader binding for controlled retry, rechecked at execution."""
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.fanout_identity import fanout_current_status
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def reader_rebuild_error(state_dir: Path, events: list[ZfEvent], payload: dict[str, Any]) -> str:
    required = ("fanout_id", "fanout_child_id", "workflow_run_id",
                "target_operation_id", "active_attempt_id", "source_event_id")
    if any(not payload.get(k) for k in required):
        return "reader rebuild requires exact fanout, child, Run, operation, attempt and failure"
    if any(e.type == "event.malformed" and e.payload.get("ledger_integrity") == "unknown" for e in events):
        return "reader rebuild ledger integrity is unknown"
    run = build_run_admission_projection(events).runs.get(payload["workflow_run_id"])
    if run is None or run.status != "running" or is_dispatch_paused(state_dir, events=events):
        return "reader rebuild Run is not dispatchable"
    identity = fanout_current_status(events, payload["fanout_id"])
    if not identity.known or not identity.current:
        return "reader rebuild fanout is stale or unknown"
    children = [e for e in events if e.type in {
        "fanout.child.dispatched", "fanout.child.failed", "fanout.child.completed",
    } and e.payload.get("fanout_id") == payload["fanout_id"]
       and e.payload.get("child_id") == payload["fanout_child_id"]]
    if (not children or children[-1].id != payload["source_event_id"]
            or children[-1].type != "fanout.child.failed" or children[-1].origin != "kernel"):
        return "reader rebuild failure is no longer current"
    failed = children[-1]
    operation = reduce_workflow_operations(events).get(payload["target_operation_id"], {})
    if (operation.get("operation_type") != "fanout_reader_child"
            or operation.get("status") not in {"failed", "suspended", "requested"}
            or operation.get("workflow_run_id") != payload["workflow_run_id"]
            or (payload.get("task_id") and payload["task_id"] != operation.get("task_id"))
            or operation.get("active_attempt_id") != payload["active_attempt_id"]
            or failed.payload.get("run_id") != payload["active_attempt_id"]
            or failed.payload.get("role_instance") != operation.get("role_instance")):
        return "reader rebuild operation binding is stale"
    try:
        request = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload["request"]
    except (ValueError, KeyError, OSError):
        return "reader rebuild request evidence is unavailable"
    if (request.get("fanout_id") != payload["fanout_id"]
            or request.get("child_id") != payload["fanout_child_id"]):
        return "reader rebuild request targets another child"
    return ""


def reader_rebuild_effect_matches(event: ZfEvent, action: dict[str, Any],
                                   events: list[ZfEvent]) -> bool:
    """Only the selected retry's terminal fact closes its recovery obligation."""
    if event.origin != "kernel":
        return False
    if event.type == "repair.action.rejected":
        request = next((e for e in events if e.id == event.causation_id), None)
        return bool(request and request.type == "repair.action.requested" and all(
            request.payload.get(k) == action.get(k) for k in (
                "target_operation_id", "active_attempt_id", "source_event_id",
                "fanout_id", "fanout_child_id", "workflow_run_id")))
    body = event.payload
    if (body.get("child_id") != action.get("fanout_child_id")
            or body.get("workflow_run_id", event.correlation_id) != action.get("workflow_run_id")):
        return False
    preceding = []
    for item in events:
        if item.id == event.id:
            break
        if (item.type == "fanout.child.dispatched" and item.origin == "kernel"
                and item.payload.get("fanout_id") == action.get("fanout_id")
                and item.payload.get("child_id") == action.get("fanout_child_id")):
            preceding.append(item)
    if not preceding:
        return False
    dispatch = preceding[-1].payload
    return bool(dispatch.get("run_id") and body.get("run_id") == dispatch["run_id"]
                and dispatch.get("operation_id") != action.get("target_operation_id")
                and (dispatch.get("payload") or {}).get("retry_of_operation_id") == action.get("target_operation_id"))
