"""Durable detection and redrive of typed OA checkpoints after pane loss."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_operations import (
    CHECKPOINT_REQUESTED,
    workflow_operation_service,
)
from zf.runtime.orchestrator_agent_role import orchestrator_instance_id
from zf.runtime.workflow_operation import (
    load_workflow_operation,
    reduce_workflow_operations,
)


_RECOVERY_SOURCE = "orchestrator_agent_checkpoint_dispatch_recovery"
_POST_DISPATCH_DEAD_REASON = "transient_transport:pane_dead:post_dispatch_probe"
_MAX_CHECKPOINT_RETRY_ATTEMPTS = 1


def request_orchestrator_agent_checkpoint_respawn(
    runtime: Any,
    *,
    operation_id: str,
    request_hash: str,
    workflow_run_id: str,
    checkpoint_event_id: str,
    trigger_event_id: str,
    causation_id: str,
    reason: str,
    source_event_type: str,
    retry_attempt: int = 1,
    max_attempts: int = _MAX_CHECKPOINT_RETRY_ATTEMPTS,
) -> ZfEvent | None:
    """Emit the single bounded OA pane-recovery request idempotently."""

    events = runtime.event_log.read_all()
    existing = next(
        (
            event
            for event in events
            if event.type == "worker.respawn.requested"
            and isinstance(event.payload, Mapping)
            and str(event.payload.get("source") or "") == _RECOVERY_SOURCE
            and str(event.payload.get("operation_id") or "") == operation_id
            and int(
                event.payload.get("checkpoint_dispatch_retry_attempt") or 0
            )
            == retry_attempt
        ),
        None,
    )
    if existing is not None:
        return None
    role_instance = orchestrator_instance_id(runtime)
    shared = {
        "role": role_instance,
        "instance_id": role_instance,
        "trigger_event_id": trigger_event_id,
        "checkpoint_event_id": checkpoint_event_id,
        "operation_id": operation_id,
        "request_hash": request_hash,
        "workflow_run_id": workflow_run_id,
        "reason": reason,
        "source_event_type": source_event_type,
        "source": _RECOVERY_SOURCE,
        "checkpoint_dispatch_retry_attempt": retry_attempt,
        "checkpoint_dispatch_retry_max_attempts": max_attempts,
    }
    requested = runtime.event_writer.append(ZfEvent(
        type="worker.respawn.requested",
        actor="zf-cli",
        origin="kernel",
        payload=shared,
        causation_id=causation_id,
        correlation_id=workflow_run_id,
    ))
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.dispatch.retry_requested",
        actor="zf-cli",
        origin="kernel",
        payload={
            **shared,
            "assignee": role_instance,
            "max_attempts": max_attempts,
        },
        causation_id=causation_id,
        correlation_id=workflow_run_id,
    ))
    return requested


def reconcile_orchestrator_agent_operation_liveness(runtime: Any) -> None:
    """Recover an OA operation whose provider died after a successful send.

    Send-time pane failure already follows this bounded respawn protocol in
    ``orchestrator_agent_transport``. This idle sweep closes the later failure
    window: an operation is ``running`` but the taskless OA process is gone.
    """

    events = runtime.event_log.read_all()
    operations = reduce_workflow_operations(events)
    _redrive_explicitly_stopped_checkpoints(runtime, events, operations)
    events = runtime.event_log.read_all()
    operations = reduce_workflow_operations(events)
    candidates = [
        operation
        for operation in operations.values()
        if str(operation.get("operation_type") or "")
        == "orchestrator_agent_semantic"
        and str(operation.get("role_instance") or "")
        == orchestrator_instance_id(runtime)
        and (
            str(operation.get("status") or "") == "running"
            or (
                str(operation.get("status") or "") == "suspended"
                and str(operation.get("reason") or "")
                == _POST_DISPATCH_DEAD_REASON
            )
        )
    ]
    if not candidates:
        return
    try:
        if runtime.transport.is_alive(orchestrator_instance_id(runtime)):
            return
    except Exception:
        # A failed liveness probe is not proof that a paid provider turn died.
        return

    for operation in candidates:
        operation_id = str(operation.get("operation_id") or "")
        request_hash = str(operation.get("request_hash") or "")
        workflow_run_id = str(operation.get("workflow_run_id") or "")
        checkpoint_event_id = str(operation.get("dispatch_id") or "")
        retry_count = int(operation.get("retry_count") or 0)
        causation_id = str(
            operation.get("last_event_id") or checkpoint_event_id
        )
        if not all((operation_id, request_hash, workflow_run_id, checkpoint_event_id)):
            raise ValueError(
                "running OA operation lacks operation/request/run/checkpoint identity"
            )
        checkpoint = next(
            (
                event
                for event in events
                if event.id == checkpoint_event_id
                and event.type == CHECKPOINT_REQUESTED
            ),
            None,
        )
        if checkpoint is None:
            workflow_operation_service(runtime).block(
                operation_id=operation_id,
                request_hash=request_hash,
                workflow_run_id=workflow_run_id,
                reason="OA pane died but checkpoint dispatch identity is missing",
                causation_id=causation_id,
                correlation_id=workflow_run_id,
            )
            continue

        status = str(operation.get("status") or "")
        failure_event = next(
            (
                event
                for event in reversed(events)
                if event.type == "orchestrator.dispatch_failed"
                and isinstance(event.payload, Mapping)
                and str(event.payload.get("operation_id") or "") == operation_id
                and str(event.payload.get("source") or "")
                == "periodic_operation_liveness"
            ),
            None,
        )
        if status == "running":
            failure_event = runtime.event_writer.append(ZfEvent(
                type="orchestrator.dispatch_failed",
                actor="zf-cli",
                origin="kernel",
                payload={
                    "trigger_event_id": checkpoint_event_id,
                    "operation_id": operation_id,
                    "error": "orchestrator provider process exited after dispatch",
                    "dead_reason": "pane_dead",
                    "source": "periodic_operation_liveness",
                },
                causation_id=causation_id,
                correlation_id=workflow_run_id,
            ))
            causation_id = failure_event.id
            if retry_count >= _MAX_CHECKPOINT_RETRY_ATTEMPTS:
                workflow_operation_service(runtime).block(
                    operation_id=operation_id,
                    request_hash=request_hash,
                    workflow_run_id=workflow_run_id,
                    reason="orchestrator checkpoint transport retry exhausted",
                    causation_id=causation_id,
                    correlation_id=workflow_run_id,
                )
                continue
            workflow_operation_service(runtime).interrupt(
                operation_id=operation_id,
                request_hash=request_hash,
                workflow_run_id=workflow_run_id,
                reason=_POST_DISPATCH_DEAD_REASON,
                causation_id=causation_id,
                correlation_id=workflow_run_id,
            )
        if retry_count >= _MAX_CHECKPOINT_RETRY_ATTEMPTS:
            continue
        request_orchestrator_agent_checkpoint_respawn(
            runtime,
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            checkpoint_event_id=checkpoint_event_id,
            trigger_event_id=checkpoint_event_id,
            causation_id=(
                failure_event.id if failure_event is not None else causation_id
            ),
            reason="pane_dead_checkpoint_dispatch_interrupted",
            source_event_type="orchestrator.dispatch_failed",
            retry_attempt=retry_count + 1,
        )


def _redrive_explicitly_stopped_checkpoints(
    runtime: Any,
    events: list[ZfEvent],
    operations: Mapping[str, Mapping[str, Any]],
) -> None:
    """Re-arm blocking OA checkpoints interrupted by an explicit stop."""

    candidates = [
        operation
        for operation in operations.values()
        if (
            str(operation.get("operation_type") or "")
            == "orchestrator_agent_semantic"
            and str(operation.get("role_instance") or "")
            == orchestrator_instance_id(runtime)
            and str(operation.get("status") or "") == "suspended"
            and str(operation.get("reason") or "")
            in {"graceful_stop", "fast_stop"}
        )
    ]
    originals = {
        str(operation.get("operation_id") or ""): _original_checkpoint_event(
            events,
            operation_id=str(operation.get("operation_id") or ""),
        )
        for operation in candidates
    }
    original_event_ids = {
        event.id for event in originals.values() if event is not None
    }
    latest_plan_candidates: dict[str, ZfEvent] = {}
    for event in events:
        if event.id not in original_event_ids or not isinstance(
            event.payload, Mapping
        ):
            continue
        if str(event.payload.get("checkpoint") or "") != "plan_candidate":
            continue
        run_id = str(event.payload.get("workflow_run_id") or "")
        latest_plan_candidates[run_id] = event

    for operation in candidates:
        operation_id = str(operation.get("operation_id") or "")
        request_hash = str(operation.get("request_hash") or "")
        workflow_run_id = str(operation.get("workflow_run_id") or "")
        interruption_event_id = str(operation.get("last_event_id") or "")
        active_attempt_id = str(operation.get("active_attempt_id") or "")
        if not all((
            operation_id,
            request_hash,
            workflow_run_id,
            interruption_event_id,
            active_attempt_id,
        )):
            workflow_operation_service(runtime).block(
                operation_id=operation_id,
                request_hash=request_hash,
                workflow_run_id=workflow_run_id,
                reason="explicit-stop OA recovery identity is incomplete",
                causation_id=interruption_event_id,
                correlation_id=workflow_run_id,
            )
            continue
        original = originals.get(operation_id)
        if original is None:
            workflow_operation_service(runtime).block(
                operation_id=operation_id,
                request_hash=request_hash,
                workflow_run_id=workflow_run_id,
                reason="explicit-stop OA recovery checkpoint is missing",
                causation_id=interruption_event_id,
                correlation_id=workflow_run_id,
            )
            continue
        latest_plan = latest_plan_candidates.get(workflow_run_id)
        if (
            isinstance(original.payload, Mapping)
            and str(original.payload.get("checkpoint") or "")
            == "plan_candidate"
            and latest_plan is not None
            and latest_plan.id != original.id
        ):
            workflow_operation_service(runtime).cancel(
                operation_id=operation_id,
                request_hash=request_hash,
                workflow_run_id=workflow_run_id,
                reason="OA plan_candidate superseded by newer revision",
                causation_id=latest_plan.id,
                correlation_id=workflow_run_id,
            )
            continue
        redrive = workflow_operation_service(runtime).admit_redrive(
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id="",
            source_attempt_id=active_attempt_id,
            recovery_decision_event_id=interruption_event_id,
            reason="OA checkpoint replay admitted after explicit runtime stop",
            recovery_decision_owner="kernel_replay",
        )
        if redrive is None:
            redrive = next(
                (
                    event
                    for event in events
                    if event.type == "workflow.operation.redrive_admitted"
                    and isinstance(event.payload, Mapping)
                    and str(event.payload.get("operation_id") or "")
                    == operation_id
                    and str(
                        event.payload.get("recovery_decision_event_id") or ""
                    )
                    == interruption_event_id
                ),
                None,
            )
        if redrive is None:
            continue
        if any(
            event.type == CHECKPOINT_REQUESTED
            and isinstance(event.payload, Mapping)
            and str(
                event.payload.get("restart_interruption_event_id") or ""
            )
            == interruption_event_id
            for event in runtime.event_log.read_all()
        ):
            continue
        original_payload = dict(original.payload)
        runtime.event_writer.append(ZfEvent(
            type=CHECKPOINT_REQUESTED,
            actor="zf-cli",
            origin="kernel",
            payload={
                **original_payload,
                "original_checkpoint_event_id": original.id,
                "restart_interruption_event_id": interruption_event_id,
                "restart_redrive_event_id": redrive.id,
            },
            causation_id=redrive.id,
            correlation_id=workflow_run_id,
        ))


def _original_checkpoint_event(
    events: list[ZfEvent],
    *,
    operation_id: str,
) -> ZfEvent | None:
    return next(
        (
            event
            for event in events
            if event.type == CHECKPOINT_REQUESTED
            and isinstance(event.payload, Mapping)
            and str(event.payload.get("operation_id") or "") == operation_id
            and not str(
                event.payload.get("restart_interruption_event_id") or ""
            )
        ),
        None,
    )


def requeue_orchestrator_agent_checkpoint_after_respawn(
    runtime: Any,
    request: ZfEvent,
    *,
    instance_id: str,
) -> ZfEvent | None:
    payload = request.payload if isinstance(request.payload, Mapping) else {}
    if (
        instance_id != orchestrator_instance_id(runtime)
        or str(payload.get("source") or "") != _RECOVERY_SOURCE
    ):
        return None
    operation_id = str(payload.get("operation_id") or "").strip()
    checkpoint_event_id = str(payload.get("checkpoint_event_id") or "").strip()
    workflow_run_id = str(payload.get("workflow_run_id") or "").strip()
    retry_attempt = int(payload.get("checkpoint_dispatch_retry_attempt") or 0)
    max_attempts = int(payload.get("checkpoint_dispatch_retry_max_attempts") or 0)
    if not operation_id or not checkpoint_event_id or not workflow_run_id:
        raise ValueError("OA respawn retry requires operation/checkpoint/run identity")
    if retry_attempt != 1 or max_attempts != 1:
        raise ValueError("OA checkpoint transport recovery is bounded to one retry")

    events = runtime.event_log.read_all()
    if any(
        event.type == CHECKPOINT_REQUESTED
        and isinstance(event.payload, Mapping)
        and str(event.payload.get("respawn_request_event_id") or "") == request.id
        for event in events
    ):
        return None
    operation = load_workflow_operation(runtime.event_log, operation_id)
    status = str((operation or {}).get("status") or "")
    reason = str((operation or {}).get("reason") or "")
    if operation is None or (
        status != "suspended" and not (status == "failed" and "pane_dead" in reason)
    ):
        raise ValueError(
            f"OA checkpoint operation {operation_id!r} is not transport-retryable"
        )
    original = next(
        (event for event in events if event.id == checkpoint_event_id),
        None,
    )
    if original is None or original.type != CHECKPOINT_REQUESTED:
        raise ValueError("original OA checkpoint event is missing")
    original_payload = (
        dict(original.payload) if isinstance(original.payload, Mapping) else {}
    )
    if (
        str(original_payload.get("operation_id") or "") != operation_id
        or str(original_payload.get("workflow_run_id") or "") != workflow_run_id
    ):
        raise ValueError("OA checkpoint retry identity does not match original event")
    return runtime.event_writer.append(ZfEvent(
        type=CHECKPOINT_REQUESTED,
        actor="zf-cli",
        origin="kernel",
        payload={
            **original_payload,
            "checkpoint_dispatch_retry_attempt": retry_attempt,
            "checkpoint_dispatch_retry_max_attempts": max_attempts,
            "original_checkpoint_event_id": checkpoint_event_id,
            "respawn_request_event_id": request.id,
        },
        causation_id=request.id,
        correlation_id=workflow_run_id,
    ))


__all__ = [
    "reconcile_orchestrator_agent_operation_liveness",
    "requeue_orchestrator_agent_checkpoint_after_respawn",
    "request_orchestrator_agent_checkpoint_respawn",
]
