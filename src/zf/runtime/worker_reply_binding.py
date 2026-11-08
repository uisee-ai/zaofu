"""Validate explicit operation targets immediately before operator guidance."""
from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent

from zf.runtime.workflow_operation import reduce_workflow_operations


def resolve_reply_operation(runtime: Any, instance_id: str, payload: dict) -> dict:
    """Unbound resident messages remain valid; explicit targets never fall back."""
    operation_id = str(payload.get("operation_id") or "").strip()
    if not operation_id:
        return {}
    operation = reduce_workflow_operations(runtime.event_log.read_all()).get(operation_id)
    if operation is None:
        raise ValueError("reply target operation not found")
    if operation.get("status") != "running":
        raise ValueError("reply target operation is not running")
    if operation.get("call_result_admitted_event_id") or operation.get("admitted_call_result_ref"):
        raise ValueError("reply target result is already admitted")
    if operation.get("role_instance") != instance_id:
        raise ValueError("reply target worker changed")
    for supplied, current in (
        ("task_id", "task_id"), ("attempt_id", "active_attempt_id"),
        ("dispatch_id", "dispatch_id"), ("lease_id", "lease_id"),
        ("workflow_run_id", "workflow_run_id"), ("run_id", "workflow_run_id"),
    ):
        value = str(payload.get(supplied) or "").strip()
        if value and value != str(operation.get(current) or ""):
            raise ValueError(f"reply target {supplied} changed")
    return operation


def admit_worker_reply(runtime: Any, request: ZfEvent, instance_id: str, payload: dict) -> bool:
    try:
        resolve_reply_operation(runtime, instance_id, payload)
    except ValueError as exc:
        runtime._emit_worker_action_failed(
            request, "worker.reply.failed", instance_id=instance_id, reason=str(exc),
        )
        return False
    return True
