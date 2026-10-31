"""TaskAttempt settlement from an admitted durable operation result."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from zf.core.events.model import ZfEvent


_NON_RESULT_TERMINAL_EVENTS = frozenset({
    "workflow.operation.failed",
    "workflow.operation.blocked",
    "workflow.operation.superseded",
    "workflow.operation.interrupted",
    "workflow.operation.cancelled",
})


def settle_failed_attempt_operations(
    runtime: Any, rows: list[dict[str, Any]], *, events: list[ZfEvent] | None = None,
) -> int:
    """Close live operations whose matching canonical attempt already failed."""
    from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations

    failed = [row for row in rows if row.get("status") == "failed"
              and row.get("failure_class") == "semantic_result_failed"
              and row.get("retryable") is False and row.get("recovery_owner") == "workflow"]
    if not failed:
        return 0
    events = events if events is not None else runtime.event_log.read_all()
    by_id = {event.id: event for event in events}
    operations = reduce_workflow_operations(events)
    service = WorkflowOperationService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    changed = 0
    for row in failed:
        operation_id = str(row.get("operation_id") or "")
        operation = operations.get(operation_id, {})
        source = by_id.get(str(row.get("terminal_event_id") or ""))
        dispatch_id = str(row.get("dispatch_id") or "")
        if (operation.get("status") != "running" or not operation.get("request_hash")
                or source is None or source.task_id != row.get("task_id") or not dispatch_id
                or str(operation.get("dispatch_id") or "") != dispatch_id
                or operation.get("workflow_run_id") != row.get("run_id")
                or operation.get("task_id") != row.get("task_id")):
            continue
        event = service.fail(
            operation_id=operation_id, request_hash=operation["request_hash"],
            workflow_run_id=str(row["run_id"]), task_id=str(row["task_id"]),
            reason=str(row.get("failure_reason") or "semantic_result_failed"),
            causation_id=source.id, correlation_id=str(row["run_id"]),
            details={"source_attempt_id": str(row["attempt_id"]),
                     "source_event_id": source.id, "source_event_type": source.type,
                     "dispatch_id": dispatch_id, "failure_class": "semantic_result_failed",
                     "retryable": False, "recovery_owner": "workflow"},
        )
        if event is not None:
            operation["status"] = "failed"
            changed += 1
    return changed


def settle_admitted_operation_attempt(
    runtime: Any,
    event: ZfEvent,
    *,
    events_by_id: Mapping[str, ZfEvent] | None = None,
    attempt_rows: list[dict[str, Any]] | None = None,
    store: Any = None,
) -> bool:
    from zf.runtime import task_attempt_runtime as support

    payload = support._payload(event)
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    operation_id = str(payload.get("operation_id") or "").strip()
    run_id = str(payload.get("workflow_run_id") or "").strip()
    admitted_ref = payload.get("admitted_call_result_ref")
    source_event_id = (
        str(admitted_ref.get("source_event_id") or "").strip()
        if isinstance(admitted_ref, Mapping)
        else ""
    )
    if not task_id or not operation_id or not source_event_id:
        return False
    if events_by_id is None:
        events_by_id = {
            row.id: row
            for row in runtime.event_log.read_all()
            if row.id
        }
    source_event = events_by_id.get(source_event_id)
    if source_event is None:
        return False
    terminal = support._admitted_result_status(source_event.type)
    if not terminal:
        return False
    source_payload = support._payload(source_event)
    dispatch_candidates = {
        str(source_payload.get(key) or "").strip()
        for key in ("dispatch_id", "attempt_id", "run_id")
        if str(source_payload.get(key) or "").strip()
    }
    attempt_store = store or support.task_attempt_store(runtime)
    candidates = [
        row
        for row in (
            attempt_rows
            if attempt_rows is not None
            else attempt_store.rows()
        )
        if str(row.get("task_id") or "") == task_id
        and str(row.get("operation_id") or "") == operation_id
        and (not run_id or str(row.get("run_id") or "") == run_id)
    ]
    # Result repair keeps the immutable CallRequest attempt. The admitted
    # fanout occurrence identifies the execution dispatch that actually ended.
    admitted_dispatches = {
        str(row.payload.get("run_id") or "")
        for row in events_by_id.values()
        if row.type == "fanout.child.completed" and row.origin == "kernel"
        and row.causation_id == source_event_id
        and row.task_id == task_id
        and row.payload.get("result_event_id") == source_event_id
        and row.payload.get("operation_id") == operation_id
        and row.payload.get("workflow_run_id") == run_id
        and row.payload.get("run_id")
    }
    if admitted_dispatches:
        if len(admitted_dispatches) != 1:
            return False
        candidates = [row for row in candidates
                      if row.get("dispatch_id") in admitted_dispatches]
    elif dispatch_candidates:
        matched = [
            row
            for row in candidates
            if str(row.get("dispatch_id") or "") in dispatch_candidates
        ]
        if matched:
            candidates = matched
    if not candidates:
        return False
    current = max(
        candidates,
        key=lambda row: (
            str(row.get("created_at") or ""),
            int(row.get("ordinal") or 0),
        ),
    )
    status = str(current.get("status") or "")
    repair_shadow_expiry = (
        support._attempt_mode(runtime) == "shadow"
        and status in {"expired", "failed"}
        and str(current.get("failure_class") or "") == "lease_expired"
    )
    if status not in {"prepared", "delivering", "sent"} and not repair_shadow_expiry:
        return False

    attempt_id = str(current.get("attempt_id") or "")
    source_reason = str(
        source_payload.get("reason")
        or source_payload.get("summary")
        or source_payload.get("status")
        or ""
    )[:500]
    row = attempt_store.update(
        attempt_id,
        status=terminal,
        updated_at=support._now(),
        terminal_event_id=source_event.id,
        failure_reason=source_reason if terminal == "failed" else "",
        failure_class=(
            "semantic_result_failed" if terminal == "failed" else ""
        ),
        retryable=False if terminal == "failed" else None,
        recovery_owner="workflow" if terminal == "failed" else "",
    )
    if row is None:
        return False
    current.update(row)
    occurrence_type = (
        "task.attempt.succeeded"
        if terminal == "succeeded"
        else "task.attempt.failed"
    )
    support._emit_once(
        runtime,
        occurrence_type,
        attempt_id=attempt_id,
        task_id=task_id,
        payload={
            **support._identity(row),
            "source_event_id": source_event.id,
            "source_event_type": source_event.type,
            "admission_event_id": event.id,
            "status": terminal,
            "failure_class": str(row.get("failure_class") or ""),
            "retryable": row.get("retryable"),
            "recovery_owner": str(row.get("recovery_owner") or ""),
            "reconciled_shadow_expiry": repair_shadow_expiry,
        },
        correlation_id=str(row.get("run_id") or ""),
        causation_id=event.id,
    )
    support._emit_shadow_comparison(runtime, row)
    return True


def settle_terminal_operation_attempt(
    runtime: Any,
    event: ZfEvent,
    *,
    events_by_id: Mapping[str, ZfEvent] | None = None,
    attempt_rows: list[dict[str, Any]] | None = None,
    store: Any = None,
) -> bool:
    """Project every terminal Workflow Operation into its active attempt."""

    if event.type == "workflow.operation.settled":
        return settle_admitted_operation_attempt(
            runtime,
            event,
            events_by_id=events_by_id,
            attempt_rows=attempt_rows,
            store=store,
        )
    if event.type not in _NON_RESULT_TERMINAL_EVENTS:
        return False

    from zf.runtime import task_attempt_runtime as support

    payload = support._payload(event)
    operation_id = str(payload.get("operation_id") or "").strip()
    run_id = str(payload.get("workflow_run_id") or "").strip()
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    if not operation_id:
        return False
    attempt_store = store or support.task_attempt_store(runtime)
    candidates = [
        row
        for row in (
            attempt_rows
            if attempt_rows is not None
            else attempt_store.rows()
        )
        if str(row.get("operation_id") or "") == operation_id
        and (not run_id or str(row.get("run_id") or "") == run_id)
        and (not task_id or str(row.get("task_id") or "") == task_id)
    ]
    source_attempt_id = str(payload.get("source_attempt_id") or "").strip()
    if source_attempt_id:
        candidates = [
            row
            for row in candidates
            if source_attempt_id
            in {
                str(row.get("attempt_id") or ""),
                str(row.get("dispatch_id") or ""),
            }
        ]
    else:
        candidates = [
            row
            for row in candidates
            if _attempt_existed_when_event_occurred(row, event)
        ]
    active = [
        row for row in candidates
        if str(row.get("status") or "") in {"prepared", "delivering", "sent"}
    ]
    provider_failure = _provider_failure_class(event)
    if not active:
        repaired = False
        for row in candidates:
            if (provider_failure and row.get("status") == "failed"
                    and row.get("terminal_event_id") == event.id
                    and row.get("recovery_owner") == "workflow"
                    and row.get("failure_class") == "workflow_operation_terminal"):
                updated = attempt_store.update(
                    str(row["attempt_id"]), status="failed", updated_at=support._now(),
                    recovery_owner="run_manager", failure_class=provider_failure,
                )
                if updated is not None:
                    row.update(updated)
                    repaired = True
        return repaired
    current = max(
        active,
        key=lambda row: (
            str(row.get("created_at") or ""),
            int(row.get("ordinal") or 0),
        ),
    )
    attempt_id = str(current.get("attempt_id") or "")
    superseded = event.type in {
        "workflow.operation.superseded",
        "workflow.operation.interrupted",
        "workflow.operation.cancelled",
    }
    reason = str(payload.get("reason") or event.type)[:500]
    recovery_owner = "run_manager" if provider_failure else "workflow"
    row = attempt_store.update(
        attempt_id,
        status="superseded" if superseded else "failed",
        updated_at=support._now(),
        terminal_event_id=event.id,
        failure_reason="" if superseded else reason,
        failure_class="" if superseded else provider_failure or "workflow_operation_terminal",
        retryable=False,
        recovery_owner=recovery_owner,
    )
    if row is None:
        return False
    current.update(row)
    ledger_ref = _seal_active_read_ledger(runtime, attempt_id)
    occurrence_type = (
        "task.attempt.superseded" if superseded else "task.attempt.failed"
    )
    support._emit_once(
        runtime,
        occurrence_type,
        attempt_id=attempt_id,
        task_id=str(row.get("task_id") or task_id),
        payload={
            **support._identity(row),
            "source_event_id": event.id,
            "source_event_type": event.type,
            "status": str(row.get("status") or ""),
            "reason": reason,
            "failure_class": str(row.get("failure_class") or ""),
            "retryable": False,
            "recovery_owner": recovery_owner,
            "read_ledger_ref": ledger_ref,
        },
        correlation_id=str(row.get("run_id") or run_id),
        causation_id=event.id,
    )
    support._emit_shadow_comparison(runtime, row)
    return True


def _provider_failure_class(event: ZfEvent) -> str:
    """Only structured terminal provider facts transfer recovery ownership."""
    from zf.runtime.provider_stop import KNOWN_STOP_REASONS

    if event.type not in {"workflow.operation.blocked", "workflow.operation.failed"}:
        return ""
    reason = str(event.payload.get("provider_stop_reason") or "")
    failure_class = str(event.payload.get("failure_class") or "")
    if (reason in KNOWN_STOP_REASONS and not reason.startswith("completed_")
            and failure_class == f"provider_stop:{reason}"):
        return failure_class
    return ""


def _attempt_existed_when_event_occurred(
    attempt: Mapping[str, Any],
    event: ZfEvent,
) -> bool:
    """Prevent an old operation terminal event from settling a later redrive."""

    created_at = str(attempt.get("created_at") or "").strip()
    event_at = str(event.ts or "").strip()
    if not created_at or not event_at:
        return True
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00")) <= (
            datetime.fromisoformat(event_at.replace("Z", "+00:00"))
        )
    except (TypeError, ValueError):
        return True


def _seal_active_read_ledger(runtime: Any, attempt_id: str) -> dict[str, Any]:
    from zf.runtime.artifact_read_ledger import (
        active_read_ledger_path,
        seal_read_ledger,
    )

    if not active_read_ledger_path(runtime.state_dir, attempt_id).is_file():
        return {}
    try:
        return seal_read_ledger(runtime.state_dir, attempt_id)
    except (OSError, ValueError):
        return {}


__all__ = [
    "settle_admitted_operation_attempt",
    "settle_terminal_operation_attempt",
]
