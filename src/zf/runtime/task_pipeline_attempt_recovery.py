"""Operation-v2 TaskAttempt expiry and recovery ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import (
    TASK_ATTEMPT_IDENTITY_OPERATION_V2,
    TASK_ATTEMPT_IDENTITY_ROLE_V1,
    TaskAttemptStore,
)


def task_attempt_identity_version(task_pipeline_stage: str) -> str:
    return (
        TASK_ATTEMPT_IDENTITY_OPERATION_V2
        if str(task_pipeline_stage or "").strip()
        else TASK_ATTEMPT_IDENTITY_ROLE_V1
    )


def is_task_pipeline_attempt(row: Mapping[str, Any]) -> bool:
    return str(row.get("identity_version") or "") == (
        TASK_ATTEMPT_IDENTITY_OPERATION_V2
    )


def live_task_pipeline_attempt_for_role(runtime: Any, instance_id: str) -> dict[str, Any] | None:
    """Fence legacy watchdog recovery while an operation-v2 attempt is live."""
    rows = TaskAttemptStore(Path(runtime.state_dir) / "task_attempts.json").current_rows()
    matching = [row for row in rows if is_task_pipeline_attempt(row)
                and row.get("instance_id") == instance_id
                and row.get("status") in {"prepared", "delivering", "sent"}]
    return dict(matching[0]) if matching else None


def task_pipeline_attempt_identity_fields(
    row: Mapping[str, Any],
) -> dict[str, str]:
    identity_version = str(row.get("identity_version") or "")
    result = {"identity_version": identity_version} if identity_version else {}
    placement_epoch = int(row.get("placement_epoch") or 0)
    if placement_epoch:
        result["placement_epoch"] = str(placement_epoch)
    return result


def mark_expired_task_pipeline_attempts(
    store: TaskAttemptStore,
    rows: Iterable[Mapping[str, Any]],
    *,
    updated_at: str,
) -> None:
    for row in rows:
        if is_task_pipeline_attempt(row):
            store.update(
                str(row.get("attempt_id") or ""),
                status="expired",
                updated_at=updated_at,
                recovery_owner="run_manager",
            )


def reconcile_expired_task_pipeline_attempt(
    runtime: Any,
    row: Mapping[str, Any],
    *,
    events: Iterable[ZfEvent],
) -> int | None:
    """Suspend the operation and wait for one exact Run Manager decision."""

    if not is_task_pipeline_attempt(row):
        return None
    event_rows = list(events)
    attempt_id = str(row.get("attempt_id") or "")
    from zf.runtime.workflow_operation import (
        WorkflowOperationService,
        reduce_workflow_operations,
    )

    operation_id = str(row.get("operation_id") or "")
    operation = reduce_workflow_operations(runtime.event_log.read_all()).get(
        operation_id
    )
    interrupted = None
    if operation is not None:
        interrupted = WorkflowOperationService(
            state_dir=Path(runtime.state_dir),
            event_log=runtime.event_log,
            event_writer=runtime.event_writer,
        ).interrupt(
            operation_id=operation_id,
            request_hash=str(operation.get("request_hash") or ""),
            workflow_run_id=str(row.get("run_id") or ""),
            task_id=str(row.get("task_id") or ""),
            reason="task_pipeline_attempt_lease_expired",
            source_attempt_id=attempt_id,
            correlation_id=str(row.get("run_id") or ""),
        )
    failure_recorded = _has_attempt_event(
        event_rows,
        "task.attempt.failed",
        attempt_id,
    )
    emit_task_pipeline_attempt_failure(runtime, row, reason="lease_expired")
    if _attempt_mode(runtime) != "enforce":
        _store(runtime).update(
            attempt_id,
            status="failed",
            updated_at=_now(runtime),
            failure_reason="lease_expired_shadow_only",
        )
        return 1
    return int(interrupted is not None or not failure_recorded)


def suspend_retryable_task_pipeline_provider_stop(
    runtime: Any,
    row: Mapping[str, Any],
    *,
    reason: str,
    causation_id: str,
    correlation_id: str,
) -> ZfEvent | None:
    """Release one provider-stopped attempt for Run Manager-owned redrive."""

    if not is_task_pipeline_attempt(row):
        return None
    attempt_id = str(row.get("attempt_id") or "")
    operation_id = str(row.get("operation_id") or "")
    if not attempt_id or not operation_id:
        return None
    store = _store(runtime)
    expired = store.update(
        attempt_id,
        status="expired",
        updated_at=_now(runtime),
        failure_reason=f"provider_stop:{reason}",
        failure_class="task_pipeline_provider_stop",
        retryable=True,
        recovery_owner="run_manager",
    )
    if expired is None:
        return None

    from zf.runtime.workflow_operation import (
        WorkflowOperationService,
        reduce_workflow_operations,
    )

    operation = reduce_workflow_operations(runtime.event_log.read_all()).get(
        operation_id
    )
    interrupted = None
    if operation is not None:
        interrupted = WorkflowOperationService(
            state_dir=Path(runtime.state_dir),
            event_log=runtime.event_log,
            event_writer=runtime.event_writer,
        ).interrupt(
            operation_id=operation_id,
            request_hash=str(operation.get("request_hash") or ""),
            workflow_run_id=str(row.get("run_id") or ""),
            task_id=str(row.get("task_id") or ""),
            reason=f"task_pipeline_provider_stop:{reason}",
            source_attempt_id=attempt_id,
            causation_id=causation_id,
            correlation_id=(
                str(correlation_id or "")
                or str(row.get("run_id") or "")
            ),
        )
    emit_task_pipeline_attempt_failure(
        runtime,
        expired,
        reason=f"provider_stop:{reason}",
    )
    return interrupted


def emit_task_pipeline_attempt_failure(
    runtime: Any,
    row: Mapping[str, Any],
    *,
    reason: str,
) -> bool:
    """Emit operation-v2 failure without scheduler-owned auto-retry."""

    if not is_task_pipeline_attempt(row):
        return False
    attempt_id = str(row.get("attempt_id") or "")
    task_id = str(row.get("task_id") or "")
    enforce = _attempt_mode(runtime) == "enforce"
    retryable = (
        row.get("retryable") is not False
        and int(row.get("ordinal") or 0) < _max_attempts(runtime)
    )
    failure_class = str(row.get("failure_class") or "task_attempt_failed")
    _emit_once(
        runtime,
        "task.attempt.failed",
        attempt_id=attempt_id,
        task_id=task_id,
        payload={
            **task_attempt_identity(row),
            "reason": str(reason or row.get("failure_reason") or "")[:500],
            "retryable": retryable,
            "failure_class": failure_class,
            "recovery_owner": str(row.get("recovery_owner") or "run_manager"),
            "shadow_only": not enforce,
            "mode": _attempt_mode(runtime),
            "actionability": (
                "run_manager_decision" if enforce else "shadow_only"
            ),
        },
        correlation_id=str(row.get("run_id") or ""),
    )
    if not enforce or retryable:
        return True
    _store(runtime).update(
        attempt_id,
        status="deadlettered",
        updated_at=_now(runtime),
        failure_reason=str(reason or "attempt budget exhausted")[:500],
        failure_class=failure_class,
        retryable=False,
    )
    _emit_once(
        runtime,
        "task.attempt.exhausted",
        attempt_id=attempt_id,
        task_id=task_id,
        payload={
            **task_attempt_identity(row),
            "reason": str(reason or "attempt budget exhausted")[:500],
            "max_attempts": _max_attempts(runtime),
            "failure_class": failure_class,
            "retryable": False,
            "recovery_owner": "human",
        },
        correlation_id=str(row.get("run_id") or ""),
    )
    return True


def task_attempt_identity(row: Mapping[str, Any]) -> dict[str, str]:
    run_id = str(row.get("run_id") or row.get("workflow_run_id") or "")
    return {
        "schema_version": "task-attempt.v1",
        "workflow_run_id": run_id,
        "run_id": run_id,
        "task_id": str(row.get("task_id") or ""),
        "parent_task_id": str(row.get("parent_task_id") or ""),
        "operation_id": str(row.get("operation_id") or ""),
        "attempt_id": str(row.get("attempt_id") or ""),
        "lease_id": str(row.get("lease_id") or ""),
        "dispatch_id": str(row.get("dispatch_id") or ""),
        **task_pipeline_attempt_identity_fields(row),
    }


def _emit_once(
    runtime: Any,
    event_type: str,
    *,
    attempt_id: str,
    task_id: str,
    payload: dict[str, Any],
    correlation_id: str,
) -> None:
    if _has_attempt_event(runtime.event_log.read_all(), event_type, attempt_id):
        return
    runtime.event_writer.append(ZfEvent(
        type=event_type,
        actor="orchestrator",
        task_id=task_id or None,
        payload=payload,
        correlation_id=correlation_id or None,
    ))


def _has_attempt_event(
    events: Iterable[ZfEvent],
    event_type: str,
    attempt_id: str,
) -> bool:
    return any(
        event.type == event_type
        and str(_payload(event).get("attempt_id") or "") == attempt_id
        for event in events
    )


def _attempt_mode(runtime: Any) -> str:
    policy = getattr(
        getattr(getattr(runtime, "config", None), "workflow", None),
        "task_attempt",
        None,
    )
    return str(getattr(policy, "mode", "shadow") or "shadow")


def _max_attempts(runtime: Any) -> int:
    policy = getattr(
        getattr(getattr(runtime, "config", None), "workflow", None),
        "task_attempt",
        None,
    )
    return max(1, min(int(getattr(policy, "max_attempts", 3) or 3), 10))


def _store(runtime: Any) -> TaskAttemptStore:
    from zf.runtime.task_attempt_runtime import task_attempt_store

    return task_attempt_store(runtime)


def _now(runtime: Any) -> str:
    del runtime
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "emit_task_pipeline_attempt_failure",
    "is_task_pipeline_attempt",
    "mark_expired_task_pipeline_attempts",
    "reconcile_expired_task_pipeline_attempt",
    "suspend_retryable_task_pipeline_provider_stop",
    "task_attempt_identity",
    "task_attempt_identity_version",
    "task_pipeline_attempt_identity_fields",
]
