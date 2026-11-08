"""Fence worker liveness claims against current scheduler-owned attempts."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore


def heartbeat_binding_error(
    store: TaskAttemptStore, *, actor: str, task_id: str, payload: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """Validate bound workers without imposing TaskAttempts on legacy roles."""
    actor = str(actor or payload.get("instance_id") or "").strip()
    task_id = str(task_id or "").strip()
    rows = store.current_rows()
    owned = [row for row in rows if actor and actor in {
        str(row.get("instance_id") or ""), str(row.get("role") or ""),
    }]
    active = [row for row in owned if row.get("status") in {
        "prepared", "delivering", "sent",
    }]
    matching = [row for row in active if row.get("task_id") == task_id]
    if active and not matching:
        return "task_id does not match the worker's active TaskAttempt", active[0]
    if not active:
        matching = [row for row in rows if task_id and row.get("task_id") == task_id]
        if not matching:
            return "", None
    for key in ("attempt_id", "dispatch_id", "operation_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            narrowed = [row for row in matching if row.get(key) == value]
            if not narrowed:
                return f"{key} does not match the current TaskAttempt", matching[0]
            matching = narrowed
    if len(matching) != 1:
        return "heartbeat has an ambiguous current TaskAttempt", None
    current = matching[0]
    if current.get("status") not in {"prepared", "delivering", "sent"}:
        return "heartbeat refers to a terminal TaskAttempt", current
    owners = {str(current.get(key) or "") for key in ("instance_id", "role")}
    if not actor or actor not in owners:
        return "heartbeat actor does not own the current TaskAttempt", current
    instance = str(payload.get("instance_id") or "").strip()
    if instance and instance not in owners:
        return "heartbeat instance_id does not own the current TaskAttempt", current
    claimed_task = str(payload.get("current_task_id") or "").strip()
    if claimed_task and claimed_task != task_id:
        return "heartbeat current_task_id differs from the event task_id", current
    for key in ("workflow_run_id", "run_id", "lease_id"):
        value = str(payload.get(key) or "").strip()
        expected = current.get("run_id" if key == "workflow_run_id" else key)
        if value and value != expected:
            return f"{key} does not match the current TaskAttempt", current
    return "", current


def admit_worker_heartbeat(runtime: Any, event: ZfEvent) -> bool:
    """Guard the real housekeeping caller before lease or liveness writes."""
    if event.type != "worker.heartbeat":
        return True
    from zf.runtime.task_attempt_runtime import _emit_once
    from zf.runtime.task_pipeline_attempt_recovery import task_attempt_identity

    error, current = heartbeat_binding_error(
        TaskAttemptStore(runtime.state_dir / "task_attempts.json"),
        actor=str(event.actor or ""), task_id=str(event.task_id or ""),
        payload=event.payload,
    )
    if not error:
        return True
    _emit_once(
        runtime, "task.attempt.result_rejected",
        attempt_id=str((current or {}).get("attempt_id") or ""),
        task_id=str(event.task_id or ""),
        payload={
            **task_attempt_identity(current or {}),
            "source_event_id": event.id, "source_event_type": event.type,
            "reason": "heartbeat_binding: " + error,
            "mode": "enforce", "actionability": "safety_verdict",
        },
        causation_id=event.id,
        correlation_id=str((current or {}).get("run_id") or event.correlation_id or ""),
        dedupe_source=event.id,
    )
    return False
