"""Consume a native stop/resume admission before ordinary transport recovery."""
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash


def pending_reader_restart(state_dir: Path, events: Sequence[ZfEvent],
                           operation: Mapping[str, Any], dispatch: ZfEvent) -> ZfEvent | None:
    if (operation.get("status") != "requested"
            or operation.get("operation_type") != "fanout_reader_child"):
        return None
    admission = next((e for e in events if e.id == operation.get("last_event_id")), None)
    if (admission is None or admission.origin != "kernel"
            or admission.type != "workflow.operation.redrive_admitted"
            or admission.payload.get("recovery_decision_owner") != "kernel_replay"
            or admission.payload.get("source_attempt_id") != operation.get("active_attempt_id")
            or any(admission.payload.get(k) != operation.get(k) for k in (
                "operation_id", "request_hash", "workflow_run_id", "task_id"))):
        return None
    decision = next((e for e in events if e.id == admission.payload.get("recovery_decision_event_id")), None)
    # The kernel admission supplies authority; legacy CLI planned events have
    # an empty origin, but must still be the exact bound mechanical checkpoint.
    if (decision is None or decision.origin not in {"", "kernel"} or decision.actor != "zf-cli"
            or decision.type != "workflow.resume.planned"
            or decision.payload.get("safe_resume_action") != "redrive_interrupted_reader_operation"
            or decision.payload.get("checkpoint_kind") != "workflow_operation"
            or any(decision.payload.get(k) != operation.get(k) for k in (
                "operation_id", "request_hash", "workflow_run_id", "task_id"))):
        return None
    data = dispatch.payload
    call = data.get("payload") or {}
    if (dispatch.origin != "kernel" or data.get("role_instance") != operation.get("role_instance")
            or any(call.get(k) != operation.get(field) for k, field in (
                ("operation_id", "operation_id"), ("request_hash", "request_hash"),
                ("attempt_id", "active_attempt_id"), ("workflow_run_id", "workflow_run_id")))):
        return None
    try:
        body = hydrate_sidecar_ref(state_dir, operation.get("request_ref") or {}).payload
        request = body["request"]
        if (operation_request_hash(body) != operation.get("request_hash")
                or request.get("fanout_id") != data.get("fanout_id")
                or request.get("child_id") != data.get("child_id")):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return decision
