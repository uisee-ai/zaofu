"""Scheduler-owned TaskAttempt lifecycle at the transport boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import (
    TaskAttemptLimitError,
    TaskAttemptStore,
)
from zf.runtime.attempt_ledger import derive_task_ledger
from zf.runtime.call_result_runtime import has_admitted_semantic_submit_provenance
from zf.runtime.run_admission import task_workflow_run_id
from zf.runtime.task_attempt_briefing import (
    bind_task_attempt_to_briefing as _bind_attempt_to_briefing,
)
from zf.runtime.task_attempt_operation_settlement import (
    settle_failed_attempt_operations,
    settle_terminal_operation_attempt,
)
from zf.runtime.task_attempt_terminal import (
    admitted_result_status as _admitted_result_status,
    clear_matching_active_dispatch as _clear_matching_active_dispatch,
    result_status as _result_status,
)
from zf.runtime.task_pipeline_attempt_recovery import (
    emit_task_pipeline_attempt_failure,
    mark_expired_task_pipeline_attempts,
    reconcile_expired_task_pipeline_attempt,
    task_attempt_identity,
    task_attempt_identity_version,
)
from zf.runtime.transport import DispatchContext


TASK_ATTEMPT_SCHEMA_VERSION = "task-attempt.v1"
class TaskAttemptDeliveryClaimedError(RuntimeError):
    """The delivery outcome is already sent or crash-ambiguous."""


@dataclass(frozen=True)
class PreparedTaskAttempt:
    context: DispatchContext
    attempt: dict[str, Any]


def task_attempt_store(runtime: Any) -> TaskAttemptStore:
    return TaskAttemptStore(Path(runtime.state_dir) / "task_attempts.json")


def active_task_attempt_identities_for_role(
    runtime: Any,
    *,
    role_name: str,
    instance_id: str,
) -> list[dict[str, str]]:
    """Return active current-attempt identities owned by one worker lane."""

    role_name = str(role_name or "").strip()
    instance_id = str(instance_id or "").strip()
    rows = [
        row
        for row in task_attempt_store(runtime).current_rows()
        if str(row.get("status") or "") in {"prepared", "delivering", "sent"}
    ]
    if instance_id:
        matches = [
            row
            for row in rows
            if str(row.get("instance_id") or "") == instance_id
        ]
    elif role_name:
        matches = [
            row
            for row in rows
            if str(row.get("role") or "") == role_name
        ]
    else:
        matches = []
    return [_identity(row) for row in matches]


def dispatch_attempt_payload(
    context: DispatchContext | None,
    *,
    include_run_alias: bool = True,
) -> dict[str, str]:
    if context is None or not context.attempt_id:
        return {}
    run_id = str(context.run_id or "")
    payload = {
        "workflow_run_id": run_id,
        "operation_id": str(context.operation_id or ""),
        "attempt_id": str(context.attempt_id or ""),
        "lease_id": str(context.lease_id or ""),
        "dispatch_id": str(context.dispatch_id or ""),
    }
    if include_run_alias:
        payload["run_id"] = run_id
    return payload


def task_operation_id(*, run_id: str, task_id: str, role_name: str) -> str:
    digest = hashlib.sha256(
        f"{run_id}|{task_id}|{role_name}".encode("utf-8")
    ).hexdigest()[:20]
    return f"op-task-{digest}"


def prepare_task_attempt(
    runtime: Any,
    *,
    context: DispatchContext | None,
    briefing_path: Path,
) -> PreparedTaskAttempt | None:
    """Persist and claim one attempt before the transport can send bytes."""

    if context is None or not context.task_id:
        return None
    dispatch_id = str(context.dispatch_id or "").strip()
    if not dispatch_id:
        # Layer-2 observation wakes can carry the triggering task id without
        # being a scheduler-owned task delivery.
        return None
    try:
        task = runtime.task_store.get(context.task_id)
    except Exception:
        task = None
    run_id = str(context.run_id or "").strip()
    if task is not None:
        task_run_id = task_workflow_run_id(task)
        if not task_run_id:
            task_run_id = task_workflow_run_id(
                task,
                events=runtime.event_log.read_all(),
            )
        run_id = task_run_id or run_id
    run_id = run_id or _session_run_id(runtime) or "legacy"
    operation_id = str(context.operation_id or "").strip() or task_operation_id(
        run_id=run_id,
        task_id=context.task_id,
        role_name=str(context.role_name or context.instance_id or ""),
    )
    now = _now()
    store = task_attempt_store(runtime)
    task_pipeline_stage = str(context.task_pipeline_stage or "").strip()
    identity_version = task_attempt_identity_version(task_pipeline_stage)
    placement_epoch = int(context.placement_epoch or 0)
    try:
        ensured = store.ensure_for_dispatch(
            run_id=run_id,
            task_id=context.task_id,
            dispatch_id=dispatch_id,
            role=str(context.role_name or ""),
            instance_id=str(context.instance_id or ""),
            operation_id=operation_id,
            briefing_ref=str(briefing_path),
            created_at=now,
            lease_expires_at=_lease_expiry(runtime),
            max_attempts=(
                _max_attempts(runtime)
                if _attempt_mode(runtime) == "enforce"
                else 0
            ),
            parent_task_id=str(context.parent_task_id or ""),
            identity_version=identity_version,
            placement_epoch=placement_epoch,
        )
    except TaskAttemptLimitError:
        _emit_attempt_exhausted(
            runtime,
            context,
            run_id=run_id,
            operation_id=operation_id,
        )
        raise

    attempt = ensured.attempt
    enriched = replace(
        context,
        run_id=run_id,
        operation_id=operation_id,
        attempt_id=str(attempt["attempt_id"]),
        lease_id=str(attempt["lease_id"]),
    )
    if ensured.superseded_attempt_id:
        superseded_attempt = (
            store.get(ensured.superseded_attempt_id) or attempt
        )
        _emit_once(
            runtime,
            "task.attempt.superseded",
            attempt_id=ensured.superseded_attempt_id,
            task_id=context.task_id,
            payload={
                **_identity(superseded_attempt),
                "attempt_id": ensured.superseded_attempt_id,
                "superseded_by": attempt["attempt_id"],
            },
            correlation_id=run_id,
        )
    _emit_once(
        runtime,
        "task.attempt.started",
        attempt_id=str(attempt["attempt_id"]),
        task_id=context.task_id,
        payload={
            **_identity(attempt),
            "ordinal": int(attempt.get("ordinal") or 0),
            "series": int(attempt.get("series") or 1),
            "role": str(attempt.get("role") or ""),
            "instance_id": str(attempt.get("instance_id") or ""),
            "briefing_ref": str(briefing_path),
            "lease_expires_at": str(attempt.get("lease_expires_at") or ""),
            "recovered": not ensured.created,
        },
        correlation_id=run_id,
    )
    _bind_attempt_to_briefing(briefing_path, attempt)
    claimed_attempt, claimed = store.claim_delivery(
        str(attempt["attempt_id"]),
        updated_at=_now(),
    )
    if not claimed:
        status = str((claimed_attempt or attempt).get("status") or "")
        if _attempt_mode(runtime) == "shadow":
            _emit_once(
                runtime,
                "task.attempt.shadow_mismatch",
                attempt_id=str(attempt["attempt_id"]),
                task_id=context.task_id,
                payload={
                    **_identity(claimed_attempt or attempt),
                    "reason": f"duplicate_delivery_candidate:{status}",
                },
                correlation_id=run_id,
            )
            return PreparedTaskAttempt(
                context=enriched,
                attempt=claimed_attempt or attempt,
            )
        raise TaskAttemptDeliveryClaimedError(
            f"TaskAttempt {attempt['attempt_id']} delivery is already {status}"
        )
    _emit_once(
        runtime,
        "task.attempt.delivery_claimed",
        attempt_id=str(attempt["attempt_id"]),
        task_id=context.task_id,
        payload=_identity(claimed_attempt or attempt),
        correlation_id=run_id,
    )
    return PreparedTaskAttempt(context=enriched, attempt=claimed_attempt or attempt)


def mark_task_attempt_sent(runtime: Any, prepared: PreparedTaskAttempt | None) -> None:
    if prepared is None:
        return
    attempt_id = str(prepared.attempt.get("attempt_id") or "")
    row = task_attempt_store(runtime).mark_sent(attempt_id, updated_at=_now())
    if row is None:
        return
    _emit_once(
        runtime,
        "task.attempt.sent",
        attempt_id=attempt_id,
        task_id=str(row.get("task_id") or ""),
        payload=_identity(row),
        correlation_id=str(row.get("run_id") or ""),
    )


def fail_task_attempt_delivery(
    runtime: Any,
    prepared: PreparedTaskAttempt | None,
    *,
    error: BaseException,
) -> None:
    if prepared is None:
        return
    attempt_id = str(prepared.attempt.get("attempt_id") or "")
    failure_class, retryable = _transport_failure_policy(error)
    row = task_attempt_store(runtime).update(
        attempt_id,
        status="failed",
        updated_at=_now(),
        failure_reason=f"transport_failure:{type(error).__name__}",
        failure_class=failure_class,
        retryable=retryable,
        recovery_owner="scheduler" if retryable else "human",
    )
    if row is None:
        return
    _emit_attempt_failure(runtime, row, reason=str(error)[:500])


def validate_task_attempt_result(
    runtime: Any,
    event: ZfEvent,
    *,
    task: Any,
) -> str:
    """Return a rejection reason in enforce mode; shadow mode only audits."""

    if _trusted_kernel_event(event) or has_admitted_semantic_submit_provenance(
        runtime, event,
    ):
        return ""
    payload = _payload(event)
    run_id = task_workflow_run_id(task)
    if not run_id:
        run_id = task_workflow_run_id(
            task,
            events=runtime.event_log.read_all(),
        )
    current = _resolve_event_attempt(
        task_attempt_store(runtime),
        event,
        run_id=run_id,
    )
    if current is None and _attempt_mode(runtime) == "shadow":
        # Compatibility for a worker turn already in flight when the
        # scheduler-owned store is first deployed.
        return ""
    problems: list[str] = []
    if current is None:
        problems.append("current_attempt_missing")
    else:
        expected = _identity(current)
        for key in (
            "workflow_run_id",
            "operation_id",
            "attempt_id",
            "lease_id",
            "dispatch_id",
        ):
            actual = str(payload.get(key) or "").strip()
            if not actual:
                problems.append(f"{key}_missing")
            elif actual != str(expected.get(key) or ""):
                problems.append(f"{key}_mismatch")
        if str(current.get("task_id") or "") != str(event.task_id or ""):
            problems.append("task_id_mismatch")
        if str(current.get("status") or "") not in {"delivering", "sent"}:
            problems.append(f"attempt_not_active:{current.get('status')}")
        if _expired(str(current.get("lease_expires_at") or "")):
            problems.append("lease_expired")
    if not problems:
        return ""

    reason = ",".join(problems)
    event_type = (
        "task.attempt.result_rejected"
        if _attempt_mode(runtime) == "enforce"
        else "task.attempt.shadow_mismatch"
    )
    _emit_result_audit(runtime, event, current=current, reason=reason, event_type=event_type)
    return reason if _attempt_mode(runtime) == "enforce" else ""


def settle_task_attempt_result(runtime: Any, event: ZfEvent) -> None:
    """Settle canonical attempt state only after ordinary lifecycle admission."""

    if event.type in {
        "task.attempt.succeeded",
        "task.attempt.failed",
        "task.attempt.superseded",
        "task.attempt.deadlettered",
    }:
        _clear_matching_active_dispatch(runtime, event)
        return
    if (
        event.type in {
            "workflow.operation.settled",
            "workflow.operation.failed",
            "workflow.operation.blocked",
            "workflow.operation.superseded",
            "workflow.operation.interrupted",
            "workflow.operation.cancelled",
        }
        and _trusted_kernel_event(event)
    ):
        settle_terminal_operation_attempt(runtime, event)
        return
    if _trusted_kernel_event(event) or event.type.startswith("task.attempt."):
        return
    terminal = _result_status(event.type)
    if not terminal or not event.task_id:
        return
    store = task_attempt_store(runtime)
    payload = _payload(event)
    current = _resolve_event_attempt(store, event)
    if current is None:
        return
    if str(current.get("status") or "") not in {
        "prepared",
        "delivering",
        "sent",
    }:
        return
    expected = _identity(current)
    if any(
        str(payload.get(key) or "").strip() != str(expected.get(key) or "")
        for key in (
            "workflow_run_id",
            "operation_id",
            "attempt_id",
            "lease_id",
            "dispatch_id",
        )
    ):
        return
    current_id = str(current.get("attempt_id") or "")
    row = store.update(
        current_id,
        status=terminal,
        updated_at=_now(),
        terminal_event_id=event.id,
        failure_reason=(
            str(payload.get("reason") or payload.get("summary") or "")[:500]
            if terminal == "failed"
            else ""
        ),
        failure_class=(
            "semantic_result_failed" if terminal == "failed" else ""
        ),
        retryable=False if terminal == "failed" else None,
        recovery_owner="workflow" if terminal == "failed" else "",
    )
    if row is None:
        return
    occurrence_type = (
        "task.attempt.succeeded" if terminal == "succeeded"
        else "task.attempt.failed"
    )
    _emit_once(
        runtime,
        occurrence_type,
        attempt_id=current_id,
        task_id=event.task_id,
        payload={
            **_identity(row),
            "source_event_id": event.id,
            "source_event_type": event.type,
            "status": terminal,
            "failure_class": str(row.get("failure_class") or ""),
            "retryable": row.get("retryable"),
            "recovery_owner": str(row.get("recovery_owner") or ""),
        },
        correlation_id=str(row.get("run_id") or ""),
        causation_id=event.id,
    )
    _emit_shadow_comparison(runtime, row)
    settle_failed_attempt_operations(runtime, [row])


def renew_task_attempt_lease(runtime: Any, event: ZfEvent) -> None:
    if event.type not in {"worker.heartbeat", "agent.usage"} or not event.task_id:
        return
    store = task_attempt_store(runtime)
    current = _resolve_event_attempt(store, event)
    if current is None:
        return
    payload = _payload(event)
    if _attempt_mode(runtime) == "enforce":
        expected = _identity(current)
        problems: list[str] = []
        for key in (
            "workflow_run_id",
            "operation_id",
            "attempt_id",
            "lease_id",
            "dispatch_id",
        ):
            actual = str(payload.get(key) or "").strip()
            if actual != str(expected.get(key) or ""):
                problems.append(
                    f"{key}_{'missing' if not actual else 'mismatch'}"
                )
        if problems:
            _emit_result_audit(
                runtime,
                event,
                current=current,
                reason="lease_renewal:" + ",".join(problems),
                event_type="task.attempt.result_rejected",
            )
            return
    actual = str(payload.get("attempt_id") or "").strip()
    if actual and actual != str(current.get("attempt_id") or ""):
        return
    row = store.renew_lease(
        str(current.get("attempt_id") or ""),
        updated_at=_now(),
        lease_expires_at=_lease_expiry(runtime),
    )
    if row is None:
        return
    _emit_once(
        runtime,
        "task.attempt.heartbeat",
        attempt_id=str(row.get("attempt_id") or ""),
        task_id=event.task_id,
        payload={
            **_identity(row),
            "lease_expires_at": str(row.get("lease_expires_at") or ""),
            "source_event_id": event.id,
        },
        correlation_id=str(row.get("run_id") or ""),
        causation_id=event.id,
        dedupe_source=event.id,
    )


def reconcile_task_attempts(runtime: Any) -> int:
    """Recover missing occurrences and turn expired leases into one retry."""

    store = task_attempt_store(runtime)
    events = runtime.event_log.read_all()
    events_by_id = {
        event.id: event
        for event in events
        if event.id
    }
    attempt_rows = store.rows()
    changed = settle_failed_attempt_operations(runtime, attempt_rows, events=events)
    for event in events:
        if (
            event.type in {
                "workflow.operation.settled",
                "workflow.operation.failed",
                "workflow.operation.blocked",
                "workflow.operation.superseded",
                "workflow.operation.interrupted",
                "workflow.operation.cancelled",
            }
            and _trusted_kernel_event(event)
            and settle_terminal_operation_attempt(
                runtime,
                event,
                events_by_id=events_by_id,
                attempt_rows=attempt_rows,
                store=store,
            )
        ):
            changed += 1
    expired_rows = store.expire(now_iso=_now(), is_expired=_expired)
    mark_expired_task_pipeline_attempts(
        store,
        expired_rows,
        updated_at=_now(),
    )
    for row in store.rows():
        attempt_id = str(row.get("attempt_id") or "")
        if not attempt_id:
            continue
        if not _has_attempt_event(events, "task.attempt.started", attempt_id):
            _emit_once(
                runtime,
                "task.attempt.started",
                attempt_id=attempt_id,
                task_id=str(row.get("task_id") or ""),
                payload={**_identity(row), "recovered": True},
                correlation_id=str(row.get("run_id") or ""),
            )
            changed += 1
        if str(row.get("status") or "") != "expired":
            continue
        pipeline_changed = reconcile_expired_task_pipeline_attempt(
            runtime,
            row,
            events=events,
        )
        if pipeline_changed is not None:
            changed += pipeline_changed
            continue
        _emit_attempt_failure(runtime, row, reason="lease_expired")
        if _attempt_mode(runtime) != "enforce":
            store.update(
                attempt_id,
                status="failed",
                updated_at=_now(),
                failure_reason="lease_expired_shadow_only",
            )
            changed += 1
            continue
        _requeue_expired_task(runtime, row)
        changed += 1
    return changed


def _settle_admitted_operation_attempt(
    runtime: Any,
    event: ZfEvent,
    *,
    events_by_id: dict[str, ZfEvent] | None = None,
    attempt_rows: list[dict[str, Any]] | None = None,
    store: TaskAttemptStore | None = None,
) -> bool:
    from zf.runtime.task_attempt_operation_settlement import (
        settle_admitted_operation_attempt,
    )

    return settle_admitted_operation_attempt(
        runtime,
        event,
        events_by_id=events_by_id,
        attempt_rows=attempt_rows,
        store=store,
    )


def _emit_attempt_failure(runtime: Any, row: dict[str, Any], *, reason: str) -> None:
    if emit_task_pipeline_attempt_failure(runtime, row, reason=reason):
        return
    attempt_id = str(row.get("attempt_id") or "")
    task_id = str(row.get("task_id") or "")
    enforce = _attempt_mode(runtime) == "enforce"
    retryable = (
        row.get("retryable") is not False
        and int(row.get("ordinal") or 0) < _max_attempts(runtime)
    )
    failure_class = str(row.get("failure_class") or "task_attempt_failed")
    recovery_owner = str(row.get("recovery_owner") or "scheduler")
    _emit_once(
        runtime,
        "task.attempt.failed",
        attempt_id=attempt_id,
        task_id=task_id,
        payload={
            **_identity(row),
            "reason": str(reason or row.get("failure_reason") or "")[:500],
            "retryable": retryable,
            "failure_class": failure_class,
            "recovery_owner": recovery_owner,
            "shadow_only": not enforce,
            "mode": _attempt_mode(runtime),
            "actionability": "retry" if enforce else "shadow_only",
        },
        correlation_id=str(row.get("run_id") or ""),
    )
    if not enforce:
        return
    if retryable:
        _emit_once(
            runtime,
            "task.attempt.retry_scheduled",
            attempt_id=attempt_id,
            task_id=task_id,
            payload={
                **_identity(row),
                "next_ordinal": int(row.get("ordinal") or 0) + 1,
                "max_attempts": _max_attempts(runtime),
                "reason": str(reason or "retryable attempt failure")[:500],
            },
            correlation_id=str(row.get("run_id") or ""),
        )
        return
    task_attempt_store(runtime).update(
        attempt_id,
        status="deadlettered",
        updated_at=_now(),
        failure_reason=str(reason or "attempt budget exhausted")[:500],
        failure_class=failure_class,
        retryable=False,
        recovery_owner="human",
    )
    _emit_once(
        runtime,
        "task.attempt.deadlettered",
        attempt_id=attempt_id,
        task_id=task_id,
        payload={
            **_identity(row),
            "max_attempts": _max_attempts(runtime),
            "reason": str(reason or "attempt budget exhausted")[:500],
            "failure_class": failure_class,
            "retryable": False,
            "recovery_owner": "human",
        },
        correlation_id=str(row.get("run_id") or ""),
    )


def _emit_attempt_exhausted(
    runtime: Any,
    context: DispatchContext,
    *,
    run_id: str,
    operation_id: str,
) -> None:
    store = task_attempt_store(runtime)
    current = next(
        (
            row
            for row in store.rows()
            if str(row.get("run_id") or "") == run_id
            and str(row.get("task_id") or "") == str(context.task_id or "")
            and str(row.get("operation_id") or "") == operation_id
            and str(row.get("role") or row.get("instance_id") or "")
            == str(context.role_name or context.instance_id or "")
            and str(row.get("status") or "") == "deadlettered"
        ),
        None,
    )
    if not current or str(current.get("status") or "") != "deadlettered":
        _emit_once(
            runtime,
            "task.attempt.deadlettered",
            attempt_id=f"budget:{run_id}:{context.task_id}",
            task_id=str(context.task_id or ""),
            payload={
                "schema_version": TASK_ATTEMPT_SCHEMA_VERSION,
                "workflow_run_id": run_id,
                "run_id": run_id,
                "task_id": str(context.task_id or ""),
                "dispatch_id": str(context.dispatch_id or ""),
                "max_attempts": _max_attempts(runtime),
                "reason": "TaskAttempt budget exhausted before dispatch",
            },
            correlation_id=run_id,
        )
    try:
        runtime.task_store.update(
            str(context.task_id or ""),
            status="blocked",
            active_dispatch_id="",
        )
    except Exception:
        pass
    getattr(runtime, "_active_dispatch_ids", {}).pop(
        str(context.task_id or ""),
        None,
    )


def _transport_failure_policy(error: BaseException) -> tuple[str, bool]:
    if isinstance(error, PermissionError):
        return "transport_permission", False
    if isinstance(error, FileNotFoundError):
        return "transport_configuration", False
    return "transport_delivery", True


def _resolve_event_attempt(
    store: TaskAttemptStore,
    event: ZfEvent,
    *,
    run_id: str = "",
) -> dict[str, Any] | None:
    payload = _payload(event)
    supplied_attempt = str(payload.get("attempt_id") or "").strip()
    if supplied_attempt:
        return store.current_for_attempt(
            task_id=str(event.task_id or ""),
            attempt_id=supplied_attempt,
        )
    if run_id:
        return store.current(
            run_id=run_id,
            task_id=str(event.task_id or ""),
        )
    return store.current_for_task(str(event.task_id or ""))


def _emit_shadow_comparison(runtime: Any, row: dict[str, Any]) -> None:
    task_id = str(row.get("task_id") or "")
    ledger = derive_task_ledger(runtime.event_log.read_all(), task_id)
    legacy_ordinal = len(ledger.attempts)
    canonical_ordinal = sum(
        1
        for attempt in task_attempt_store(runtime).rows()
        if str(attempt.get("run_id") or "") == str(row.get("run_id") or "")
        and str(attempt.get("task_id") or "") == task_id
    )
    _emit_once(
        runtime,
        "task.attempt.shadow.compared",
        attempt_id=str(row.get("attempt_id") or ""),
        task_id=task_id,
        payload={
            **_identity(row),
            "canonical_ordinal": canonical_ordinal,
            "legacy_ordinal": legacy_ordinal,
            "match": canonical_ordinal == legacy_ordinal,
        },
        correlation_id=str(row.get("run_id") or ""),
    )


def _emit_result_audit(
    runtime: Any,
    event: ZfEvent,
    *,
    current: dict[str, Any] | None,
    reason: str,
    event_type: str,
) -> None:
    row = current or {}
    _emit_once(
        runtime,
        event_type,
        attempt_id=str(row.get("attempt_id") or f"source:{event.id}"),
        task_id=str(event.task_id or ""),
        payload={
            **_identity(row),
            "source_event_id": event.id,
            "source_event_type": event.type,
            "reason": reason,
            "mode": _attempt_mode(runtime),
            "actionability": (
                "safety_verdict"
                if _attempt_mode(runtime) == "enforce"
                else "shadow_only"
            ),
            "actual": {
                key: str(_payload(event).get(key) or "")
                for key in (
                    "workflow_run_id",
                    "operation_id",
                    "attempt_id",
                    "lease_id",
                    "dispatch_id",
                )
            },
        },
        correlation_id=str(row.get("run_id") or event.correlation_id or ""),
        causation_id=event.id,
        dedupe_source=event.id,
    )


def _requeue_expired_task(runtime: Any, row: dict[str, Any]) -> None:
    task_id = str(row.get("task_id") or "")
    dispatch_id = str(row.get("dispatch_id") or "")
    try:
        task = runtime.task_store.get(task_id)
    except Exception:
        task = None
    if task is None or str(task.active_dispatch_id or "") != dispatch_id:
        return
    if int(row.get("ordinal") or 0) >= _max_attempts(runtime):
        runtime.task_store.update(
            task_id,
            status="blocked",
            active_dispatch_id="",
        )
    else:
        runtime.task_store.update(
            task_id,
            status="backlog",
            active_dispatch_id="",
        )
    getattr(runtime, "_active_dispatch_ids", {}).pop(task_id, None)


def _identity(row: dict[str, Any]) -> dict[str, str]:
    return task_attempt_identity(row)


def _emit_once(
    runtime: Any,
    event_type: str,
    *,
    attempt_id: str,
    task_id: str,
    payload: dict[str, Any],
    correlation_id: str,
    causation_id: str | None = None,
    dedupe_source: str = "",
) -> None:
    if _has_attempt_event(
        runtime.event_log.read_all(),
        event_type,
        attempt_id,
        dedupe_source=dedupe_source,
    ):
        return
    runtime.event_writer.append(ZfEvent(
        type=event_type,
        actor="orchestrator",
        task_id=task_id or None,
        payload=payload,
        causation_id=causation_id,
        correlation_id=correlation_id or None,
    ))


def _has_attempt_event(
    events: Iterable[ZfEvent],
    event_type: str,
    attempt_id: str,
    *,
    dedupe_source: str = "",
) -> bool:
    for event in events:
        if event.type != event_type:
            continue
        payload = _payload(event)
        if str(payload.get("attempt_id") or "") != attempt_id:
            continue
        if dedupe_source and str(payload.get("source_event_id") or "") != dedupe_source:
            continue
        return True
    return False


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


def _lease_expiry(runtime: Any) -> str:
    seconds = float(
        getattr(
            getattr(getattr(runtime, "config", None), "workflow", None),
            "attempt_lease_grace_s",
            900.0,
        )
        or 900.0
    )
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1.0, seconds))).isoformat()


def _expired(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _session_run_id(runtime: Any) -> str:
    try:
        return str(runtime.session_store.load().session_id or "")
    except Exception:
        return ""


def _trusted_kernel_event(event: ZfEvent) -> bool:
    return str(event.origin or "") == "kernel"


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "PreparedTaskAttempt",
    "TASK_ATTEMPT_SCHEMA_VERSION",
    "TaskAttemptDeliveryClaimedError",
    "active_task_attempt_identities_for_role",
    "dispatch_attempt_payload",
    "fail_task_attempt_delivery",
    "mark_task_attempt_sent",
    "prepare_task_attempt",
    "reconcile_task_attempts",
    "renew_task_attempt_lease",
    "settle_task_attempt_result",
    "task_operation_id",
    "task_attempt_store",
    "validate_task_attempt_result",
]
