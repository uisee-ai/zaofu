"""Validate same-attempt delivery without allocating or reclaiming an attempt."""
from datetime import datetime, timezone
from typing import Any

from zf.core.events import ZfEvent

from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.provider_turn_liveness import _parse_event_ts
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.transport import DispatchContext


def continuation_context(runtime: Any, role: str, context: Any) -> Any:
    """Preserve the current attempt when the lifecycle prompt is a follow-up."""
    from dataclasses import replace
    from zf.runtime.task_pipeline_attempt_recovery import live_task_pipeline_attempt_for_role

    attempt = live_task_pipeline_attempt_for_role(runtime, role)
    if not isinstance(context, DispatchContext) or attempt is None:
        return context
    return replace(context, continuation=True, run_id=attempt["run_id"],
        operation_id=attempt["operation_id"], attempt_id=attempt["attempt_id"],
        lease_id=attempt["lease_id"], dispatch_id=attempt["dispatch_id"])


def validate_task_continuation(runtime: Any, role: str, context: DispatchContext) -> None:
    if is_dispatch_paused(runtime.state_dir):
        raise RuntimeError("continuation is paused")
    task = runtime.task_store.get(str(context.task_id or ""))
    row = task_attempt_store(runtime).get(str(context.attempt_id or "")) or {}
    expected = {"run_id": context.run_id, "task_id": context.task_id,
        "operation_id": context.operation_id, "attempt_id": context.attempt_id,
        "dispatch_id": context.dispatch_id, "lease_id": context.lease_id, "instance_id": role}
    if (row.get("status") != "sent" or any(not value or row.get(key) != value for key, value in expected.items())
            or task is None or task.status != "in_progress" or task.assigned_to != role
            or task.active_dispatch_id != context.dispatch_id):
        raise RuntimeError("continuation requires the current sent attempt and task owner")
    expiry = _parse_event_ts(row.get("lease_expires_at"))
    if not expiry or expiry <= datetime.now(timezone.utc):
        raise RuntimeError("continuation lease expired")
    from zf.runtime.workflow_operation import load_workflow_operation
    operation = load_workflow_operation(runtime.event_log, str(context.operation_id)) or {}
    if (operation.get("status") != "running" or operation.get("active_attempt_id") != context.attempt_id
            or operation.get("dispatch_id") != context.dispatch_id or operation.get("lease_id") != context.lease_id):
        raise RuntimeError("continuation operation identity is stale")
    if not runtime.transport.is_alive(role):
        raise RuntimeError("continuation requires the existing live worker; recovery must resolve its absence")


def claim_completion_nudge(runtime: Any, role: str, task_id: str, dispatch_id: str,
                           *, causation_id: str | None, correlation_id: str | None) -> ZfEvent | None:
    """Claim before bytes leave; an interrupted delivery remains explicitly unknown."""
    import hashlib
    from zf.core.state.locks import locked_path
    from zf.runtime.attempt_read_progress import progress_nudge_state

    key = hashlib.sha256(f"{role}|{task_id}|{dispatch_id}".encode()).hexdigest()
    with locked_path(runtime.state_dir / "locks" / f"continuation-{key}"):
        progress = progress_nudge_state(runtime, role_instance=role, task_id=task_id, dispatch_id=dispatch_id)
        if progress is None or not progress[2]:
            return None
        claim = runtime.event_writer.append(ZfEvent(type="worker.continuation.claimed", origin="kernel",
            actor=role, task_id=task_id, causation_id=causation_id or "", correlation_id=correlation_id,
            payload={"dispatch_id": dispatch_id, "attempt_id": progress[0], "input_read_progress": progress[1],
                "recovery_action": "completion_nudge_requested", "delivery_status": "unknown",
                "recovery_owner": "run_manager", "next_action": "reconcile_existing_turn_before_retry"}))
    return claim
