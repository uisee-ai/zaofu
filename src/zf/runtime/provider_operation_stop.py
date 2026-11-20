"""Converge provider-stop signals with durable Operation/TaskAttempt state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.workflow_operation import (
    TERMINAL_OPERATION_STATUSES,
    load_workflow_operation,
)
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


@dataclass(frozen=True)
class ProviderOperationStopOutcome:
    operation_id: str
    attempt_id: str
    operation_type: str
    terminal_event_id: str
    failure_projected: bool
    task_pipeline_recovery_owned: bool = False


def emit_provider_stop_recovery(
    runtime: Any,
    event: ZfEvent,
    *,
    task: Any,
    reason: str,
    action: str,
    role: Any,
    cooldown_until: str = "",
) -> None:
    """Project provider health and recovery without requiring a Task record."""

    try:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        task_id = str(
            getattr(task, "id", "")
            or event.task_id
            or payload.get("task_id")
            or ""
        )
        recovery_payload = {
            "reason": reason,
            "action": action,
            "origin_event": event.type,
            "origin_event_id": event.id,
            "assigned_to": str(
                getattr(task, "assigned_to", "")
                or payload.get("instance_id")
                or event.actor
                or ""
            ),
            "role": role.name if role is not None else "",
            "instance_id": role.instance_id if role is not None else "",
            "backend": role.backend if role is not None else "",
            "dispatch_id": str(
                getattr(task, "active_dispatch_id", "")
                or payload.get("dispatch_id")
                or ""
            ),
            "operation_id": str(payload.get("operation_id") or ""),
            "cooldown_until": cooldown_until,
        }
        if reason in {"quota_exhausted", "rate_limited"}:
            operation = load_workflow_operation(runtime.event_log, recovery_payload["operation_id"]) or {}
            recovery_payload["external_wait"] = {
                "kind": reason, "source_event_id": event.id,
                "workflow_run_id": operation.get("workflow_run_id") or event.correlation_id or "",
                "operation_id": operation.get("operation_id", ""),
                "active_attempt_id": operation.get("active_attempt_id", ""),
                "request_hash": operation.get("request_hash", ""),
                "backend": recovery_payload["backend"],
                "model": str(getattr(role, "model", "") or ""),
                "account_scope": "unconfirmed", "probe_status": "not_configured",
                "resume_condition": "authorized_availability_evidence_and_controlled_recovery",
                "authorizes_execution": False,
            }
        runtime.event_writer.append(ZfEvent(
            type="provider.stop.recovery",
            actor="zf-cli",
            task_id=task_id or None,
            payload=recovery_payload,
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        status = (
            "exhausted" if reason == "quota_exhausted"
            else "blocked" if action == "suspend"
            else "cooldown" if action == "cooldown"
            else "degraded"
        )
        runtime.event_writer.append(ZfEvent(
            type="provider.health.changed",
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                **recovery_payload,
                "status": status,
                "requires_operator": action == "suspend" or reason == "quota_exhausted",
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        if action == "cooldown":
            runtime.event_writer.append(ZfEvent(
                type="provider.cooldown.started",
                actor="zf-cli",
                task_id=task_id or None,
                payload={
                    **recovery_payload,
                    "status": "cooldown",
                    "cooldown_seconds": getattr(
                        runtime,
                        "_layer2_cooldown_s",
                        60.0,
                    ),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
    except Exception:
        pass


def recover_provider_stopped_operation(
    runtime: Any,
    event: ZfEvent,
    reason: str,
    action: str,
    role: Any,
    task: Any,
    assignee: str,
) -> WorkflowRuntimeDecision | None:
    """Close durable state and own retryable reader recovery end to end."""

    outcome = settle_provider_stopped_operation(
        runtime,
        event,
        reason=reason,
        action=action,
    )
    if outcome is None:
        return None
    if outcome.task_pipeline_recovery_owned:
        if role is not None:
            try:
                runtime._set_worker_state(
                    role.instance_id,
                    "pending_recycle",
                    reason=f"provider stop {reason}",
                )
            except Exception:
                pass
        emit_provider_stop_recovery(
            runtime,
            event,
            task=task,
            reason=reason,
            action="task_pipeline_redrive",
            role=role,
        )
        return WorkflowRuntimeDecision(
            action="recover",
            task_id=str(event.task_id or getattr(task, "id", "") or ""),
            role=assignee,
            reason=(
                f"provider stop {reason} -> Task Pipeline operation suspended "
                "for Run Manager redrive"
            ),
        )
    if not outcome.failure_projected:
        # Run-scoped operations such as Orchestrator Agent checkpoints do not
        # own a canonical Task. Their provider stop must still terminalize the
        # durable operation; otherwise workflow resume sees a phantom running
        # operation forever. Task-bound operations continue through the
        # existing task recovery branches below.
        if task is not None and outcome.operation_type != "fanout_reader_child":
            return None
        emit_provider_stop_recovery(
            runtime,
            event,
            task=task,
            reason=reason,
            action=action,
            role=role,
        )
        return WorkflowRuntimeDecision(
            action=(
                "block" if action == "suspend"
                else "skip" if action == "cooldown"
                else "recover"
            ),
            task_id=str(event.task_id or ""),
            role=assignee,
            reason=(
                f"provider stop {reason} -> durable taskless operation "
                "terminalized"
            ),
        )
    if role is not None:
        try:
            runtime._set_worker_state(
                role.instance_id,
                "pending_recycle",
                reason=f"provider stop {reason}",
            )
        except Exception:
            pass
    emit_provider_stop_recovery(
        runtime,
        event,
        task=task,
        reason=reason,
        action="fanout_failure",
        role=role,
    )
    return WorkflowRuntimeDecision(
        action="recover",
        task_id=str(event.task_id or getattr(task, "id", "") or ""),
        role=assignee,
        reason=f"provider stop {reason} -> durable reader failure projected",
    )


def settle_provider_stopped_operation(
    runtime: Any,
    event: ZfEvent,
    *,
    reason: str,
    action: str,
) -> ProviderOperationStopOutcome | None:
    """Close one exact provider-owned operation and its scheduler lease.

    Retryable reader failures are projected into the existing fanout failure
    path.  Auth/rate-limit stops close the operation without fabricating a
    semantic child verdict; their existing operator/goal-limited routes remain
    authoritative.
    """

    payload = event.payload if isinstance(event.payload, Mapping) else {}
    attempt = _active_attempt(runtime, event, payload)
    operation_id = str(
        payload.get("operation_id")
        or (attempt or {}).get("operation_id")
        or ""
    ).strip()
    if not operation_id:
        return None
    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is None:
        return None
    operation_status = str(operation.get("status") or "")
    operation_type = str(operation.get("operation_type") or "")
    if operation_status == "suspended":
        recovery_attempt = _task_pipeline_recovery_attempt(
            runtime,
            operation_id=operation_id,
            payload=payload,
        )
        if (
            operation_type == "task-stage"
            and str(operation.get("task_pipeline_stage") or "")
            and recovery_attempt is not None
        ):
            return ProviderOperationStopOutcome(
                operation_id=operation_id,
                attempt_id=str(recovery_attempt.get("attempt_id") or ""),
                operation_type=operation_type,
                terminal_event_id=str(operation.get("last_event_id") or ""),
                failure_projected=False,
                task_pipeline_recovery_owned=True,
            )
        return None
    if operation_status in TERMINAL_OPERATION_STATUSES:
        return None
    request = _operation_request(runtime, operation)
    operation_type = str(operation_type or request.get("operation_type") or "")
    retryable_stop = action in {"requeue", "recycle"}
    fanout_id = str(request.get("fanout_id") or payload.get("fanout_id") or "")
    child_id = str(request.get("child_id") or payload.get("child_id") or "")
    reader_failure = bool(
        retryable_stop
        and operation_type == "fanout_reader_child"
        and fanout_id
        and child_id
    )
    task_pipeline_recovery_owned = bool(
        retryable_stop
        and operation_type == "task-stage"
        and str(operation.get("task_pipeline_stage") or "")
        and attempt is not None
        and str(attempt.get("identity_version") or "") == "operation-v2"
    )
    workflow_run_id = str(
        operation.get("workflow_run_id")
        or (attempt or {}).get("run_id")
        or payload.get("workflow_run_id")
        or payload.get("run_id")
        or event.correlation_id
        or ""
    )
    task_id = str(
        operation.get("task_id")
        or (attempt or {}).get("task_id")
        or event.task_id
        or ""
    )
    request_hash = str(operation.get("request_hash") or "")
    details = {
        "failure_class": f"provider_stop:{reason}",
        "provider_stop_reason": reason,
        "source_event_id": event.id,
        "source_event_type": event.type,
        "operation_type": operation_type,
        "fanout_id": fanout_id,
        "stage_id": str(request.get("stage_id") or payload.get("stage_id") or ""),
        "child_id": child_id,
        "run_id": str(
            request.get("result_identity", {}).get("run_id")
            if isinstance(request.get("result_identity"), Mapping)
            else ""
        ) or str((attempt or {}).get("dispatch_id") or ""),
        "role_instance": str(
            operation.get("role_instance")
            or request.get("role_instance")
            or payload.get("instance_id")
            or event.actor
            or ""
        ),
        "status": "failed",
        "provider_failure_projection": reader_failure,
    }
    from zf.runtime.call_result_runtime import workflow_operation_service

    service = workflow_operation_service(runtime)
    if task_pipeline_recovery_owned:
        from zf.runtime.task_pipeline_attempt_recovery import (
            suspend_retryable_task_pipeline_provider_stop,
        )

        terminal = suspend_retryable_task_pipeline_provider_stop(
            runtime,
            attempt or {},
            reason=reason,
            causation_id=event.id,
            correlation_id=str(event.correlation_id or workflow_run_id),
        )
    elif retryable_stop:
        terminal = service.fail(
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            reason=f"provider_stop:{reason}",
            causation_id=event.id,
            correlation_id=event.correlation_id or workflow_run_id,
            details=details,
        )
    else:
        terminal = service.block(
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            reason=f"provider_stop:{reason}",
            causation_id=event.id,
            correlation_id=event.correlation_id or workflow_run_id,
            details=details,
        )
    if terminal is not None and not task_pipeline_recovery_owned:
        runtime._settle_task_attempt_result(terminal)
    return ProviderOperationStopOutcome(
        operation_id=operation_id,
        attempt_id=str((attempt or {}).get("attempt_id") or ""),
        operation_type=operation_type,
        terminal_event_id=terminal.id if terminal is not None else "",
        failure_projected=reader_failure,
        task_pipeline_recovery_owned=task_pipeline_recovery_owned,
    )


def _active_attempt(
    runtime: Any,
    event: ZfEvent,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    active = [
        row for row in task_attempt_store(runtime).current_rows()
        if str(row.get("status") or "") in {"prepared", "delivering", "sent"}
    ]
    filters = {
        "operation_id": str(payload.get("operation_id") or "").strip(),
        "attempt_id": str(payload.get("attempt_id") or "").strip(),
        "lease_id": str(payload.get("lease_id") or "").strip(),
        "dispatch_id": str(payload.get("dispatch_id") or "").strip(),
    }
    exact = [
        row for row in active
        if all(
            not value or str(row.get(key) or "") == value
            for key, value in filters.items()
        )
    ]
    if any(filters.values()):
        active = exact
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    if task_id:
        scoped = [row for row in active if str(row.get("task_id") or "") == task_id]
        if scoped:
            active = scoped
    actor = str(payload.get("instance_id") or event.actor or "").strip()
    if actor:
        scoped = [
            row for row in active
            if actor in {
                str(row.get("instance_id") or ""),
                str(row.get("role") or ""),
            }
        ]
        if scoped:
            active = scoped
    return dict(active[0]) if len(active) == 1 else None


def _task_pipeline_recovery_attempt(
    runtime: Any,
    *,
    operation_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    attempt_id = str(payload.get("attempt_id") or "").strip()
    candidates = [
        row for row in task_attempt_store(runtime).rows()
        if str(row.get("operation_id") or "") == operation_id
        and str(row.get("identity_version") or "") == "operation-v2"
        and str(row.get("recovery_owner") or "") == "run_manager"
        and str(row.get("status") or "") in {"expired", "deadlettered"}
        and (not attempt_id or str(row.get("attempt_id") or "") == attempt_id)
    ]
    if not candidates:
        return None
    return dict(max(
        candidates,
        key=lambda row: (
            str(row.get("updated_at") or ""),
            int(row.get("ordinal") or 0),
        ),
    ))


def _operation_request(
    runtime: Any,
    operation: Mapping[str, Any],
) -> dict[str, Any]:
    descriptor = operation.get("request_ref")
    if not isinstance(descriptor, Mapping):
        return {}
    try:
        stored = hydrate_sidecar_ref(runtime.state_dir, dict(descriptor)).payload
    except Exception:
        return {}
    request = stored.get("request") if isinstance(stored, Mapping) else None
    return dict(request) if isinstance(request, Mapping) else {}


__all__ = [
    "ProviderOperationStopOutcome",
    "emit_provider_stop_recovery",
    "recover_provider_stopped_operation",
    "settle_provider_stopped_operation",
]
