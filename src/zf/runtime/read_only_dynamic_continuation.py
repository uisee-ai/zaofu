"""Reserved, replay-safe dispatch for one registered read-only workflow pattern."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.dynamic_fragment_policy import (
    CONTINUATION_ENVELOPE_SCHEMA_VERSION,
    DYNAMIC_CONTINUATION_ACTION,
    FRAGMENT_SCHEMA_VERSION,
    action_from_fragment_proposal,
    canonical_fragment_digest,
    pattern_child_count,
    validate_read_only_fragment,
)
from zf.runtime.dynamic_continuation_state import (
    FRAGMENT_TERMINALS,
    budget_digest as _budget_digest,
    currentness_preflight as _currentness_preflight,
    emit_fragment_once as _emit_fragment_once,
    matching_dispatch_event as _matching_dispatch_event,
    operation_id as _operation_id,
    operation_request as _operation_request,
)
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
    reduce_workflow_operations,
)


RESERVATION_LEASE_SECONDS = 30

@dataclass(frozen=True)
class DynamicContinuationResult:
    status: str
    reason: str = ""
    operation_id: str = ""
    request_hash: str = ""
    reservation_id: str = ""
    idempotency_key: str = ""
    dispatch_event_id: str = ""


def pending_read_only_continuation_actions(
    state_dir: Path,
    *,
    config: ZfConfig,
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    """Project unresolved fragment proposals into Run Manager actions.

    This projector deliberately supports one registered read-only pattern per
    fragment. Arbitrary DAG materialization remains outside D0.
    """

    terminal_by_fragment: set[str] = set()
    latest_by_fragment: dict[str, ZfEvent] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fragment_id = str(payload.get("fragment_id") or "")
        if not fragment_id:
            continue
        if event.type == "workflow.fragment.proposed":
            latest_by_fragment[fragment_id] = event
        elif event.type in FRAGMENT_TERMINALS:
            terminal_by_fragment.add(fragment_id)

    operations = reduce_workflow_operations(events)
    actions: list[dict[str, Any]] = []
    for fragment_id, event in latest_by_fragment.items():
        if fragment_id in terminal_by_fragment:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        action = action_from_fragment_proposal(event, payload)
        operation_id = _operation_id(action)
        operation = operations.get(operation_id) or {}
        operation_status = str(operation.get("status") or "")
        if operation_status in {"running", "settled", "failed", "blocked", "superseded"}:
            continue
        if _matching_dispatch_event(events, operation_id) is not None:
            continue
        actions.append(action)
    return actions


def execute_read_only_continuation(
    state_dir: Path,
    *,
    config: ZfConfig,
    event_log: EventLog,
    writer: EventWriter,
    action: Mapping[str, Any],
    causation_id: str = "",
    after_reserve: Callable[[], None] | None = None,
    project_root: Path | None = None,
) -> DynamicContinuationResult:
    """Reserve and publish one existing read-only execution pattern."""

    state_dir = Path(state_dir)
    action_body = dict(action)
    continuation_key = str(action_body.get("continuation_key") or "")
    generation = str(action_body.get("task_map_generation") or "")
    guard_name = hashlib.sha256(
        f"{action_body.get('parent_operation_id')}:{generation}".encode("utf-8"),
    ).hexdigest()[:24]
    with locked_path(
        state_dir / "locks" / f"dynamic-continuation-{guard_name}",
    ):
        events = event_log.read_all()
        operation_id = _operation_id(action_body)
        existing_operation = load_workflow_operation(event_log, operation_id) or {}
        service = WorkflowOperationService(
            state_dir=state_dir,
            event_log=event_log,
            event_writer=writer,
        )
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        from zf.runtime.stage_definition_binding import pin_stage_definition, restore_stage_definition

        definition_ref = None
        binding_config = config
        if existing_operation.get("request_ref"):
            prior_request = hydrate_sidecar_ref(state_dir, existing_operation["request_ref"]).payload["request"]
            definition_ref = prior_request.get("stage_definition_ref")
            if definition_ref:
                frozen_stage = restore_stage_definition(state_dir, config, definition_ref)
                binding_config = replace(config, workflow=replace(config.workflow, stages=[frozen_stage]))
        static_reason, pattern = validate_read_only_fragment(binding_config, action_body)
        if static_reason:
            if str(existing_operation.get("status") or "") in {
                "requested",
                "reserved",
            }:
                service.supersede(
                    operation_id=operation_id,
                    request_hash=str(existing_operation.get("request_hash") or ""),
                    workflow_run_id=str(action_body["workflow_run_id"]),
                    reason=static_reason,
                    reservation_id=str(
                        existing_operation.get("reservation_id") or ""
                    ),
                    task_id=str(action_body["task_id"]),
                    causation_id=causation_id,
                    correlation_id=str(action_body["workflow_run_id"]),
                )
            _emit_fragment_once(
                writer,
                events,
                "workflow.fragment.rejected",
                action_body,
                reason=static_reason,
                causation_id=causation_id,
            )
            return DynamicContinuationResult("rejected", static_reason)

        action_body["reserved_child_count"] = pattern_child_count(binding_config, pattern)
        currentness_reason, pending_digest, budget = _currentness_preflight(
            state_dir,
            config=config,
            events=events,
            action=action_body,
        )
        if currentness_reason:
            if str(existing_operation.get("status") or "") in {
                "requested",
                "reserved",
            }:
                service.supersede(
                    operation_id=operation_id,
                    request_hash=str(existing_operation.get("request_hash") or ""),
                    workflow_run_id=str(action_body["workflow_run_id"]),
                    reason=currentness_reason,
                    reservation_id=str(
                        existing_operation.get("reservation_id") or ""
                    ),
                    task_id=str(action_body["task_id"]),
                    causation_id=causation_id,
                    correlation_id=str(action_body["workflow_run_id"]),
                )
            _emit_fragment_once(
                writer,
                events,
                "workflow.fragment.superseded",
                action_body,
                reason=currentness_reason,
                causation_id=causation_id,
            )
            return DynamicContinuationResult(
                "superseded",
                currentness_reason,
                operation_id=operation_id,
                request_hash=str(existing_operation.get("request_hash") or ""),
                reservation_id=str(
                    existing_operation.get("reservation_id") or ""
                ),
                idempotency_key=str(
                    existing_operation.get("idempotency_key") or ""
                ),
            )

        pinned_pending_digest = (
            str(existing_operation.get("pending_action_digest") or "")
            or pending_digest
        )
        pinned_budget = (
            existing_operation.get("budget_snapshot")
            if isinstance(existing_operation.get("budget_snapshot"), Mapping)
            and existing_operation.get("budget_snapshot")
            else budget
        )
        operation_request = _operation_request(
            action_body,
            pattern=pattern,
            pending_action_digest=pinned_pending_digest,
            budget_snapshot=pinned_budget,
        )
        if not existing_operation:
            definition_ref = pin_stage_definition(
                state_dir, config, str(action_body["pattern_id"]), project_root=project_root,
            )
        if definition_ref:
            operation_request["stage_definition_ref"] = dict(definition_ref)
            operation_request["reserved_child_count"] = action_body["reserved_child_count"]
        ensured = service.ensure_operation(
            workflow_run_id=str(action_body["workflow_run_id"]),
            operation_id=operation_id,
            operation_type="dynamic_read_only_workflow",
            request=operation_request,
            parent_operation_id=str(action_body["parent_operation_id"]),
            parent_stage_id=str(action_body["pattern_id"]),
            task_id=str(action_body["task_id"]),
            child_task_ids=[str(action_body["task_id"])],
            causation_id=causation_id or str(action_body.get("source_event_id") or ""),
            correlation_id=str(action_body["workflow_run_id"]),
        )
        if ensured.status in {"divergent", "failed", "blocked", "superseded"}:
            reason = ensured.reason or f"workflow_operation_{ensured.status}"
            return DynamicContinuationResult(
                "rejected",
                reason,
                operation_id=operation_id,
                request_hash=ensured.request_hash,
            )
        if ensured.status in {"running", "settled"}:
            return DynamicContinuationResult(
                "already_dispatched",
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                idempotency_key=str(
                    (load_workflow_operation(event_log, operation_id) or {}).get(
                        "idempotency_key",
                    )
                    or ""
                ),
            )

        reservation = service.reserve_continuation(
            operation_id=operation_id,
            request_hash=ensured.request_hash,
            workflow_run_id=str(action_body["workflow_run_id"]),
            continuation_key=continuation_key,
            expected_generation=generation,
            expected_package_ref=str(
                action_body["plan_artifact_package_ref"],
            ),
            expected_package_digest=str(
                action_body["plan_artifact_package_digest"],
            ),
            parent_operation_id=str(action_body["parent_operation_id"]),
            pending_action_digest=pinned_pending_digest,
            budget_snapshot=pinned_budget,
            reservation_expires_at=(
                datetime.now(timezone.utc)
                + timedelta(seconds=RESERVATION_LEASE_SECONDS)
            ).isoformat(),
            task_id=str(action_body["task_id"]),
            causation_id=causation_id or str(action_body.get("source_event_id") or ""),
            correlation_id=str(action_body["workflow_run_id"]),
        )
        if reservation.status not in {"reserved", "running"}:
            return DynamicContinuationResult(
                reservation.status,
                reservation.reason,
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                reservation_id=reservation.reservation_id,
                idempotency_key=reservation.idempotency_key,
            )

        if after_reserve is not None:
            after_reserve()

        events = event_log.read_all()
        currentness_reason, current_pending_digest, current_budget = (
            _currentness_preflight(
                state_dir,
                config=config,
                events=events,
                action=action_body,
            )
        )
        if (
            not currentness_reason
            and current_pending_digest != pinned_pending_digest
        ):
            currentness_reason = "pending_operator_or_control_action_changed"
        if not currentness_reason and _budget_digest(current_budget) != _budget_digest(
            pinned_budget,
        ):
            currentness_reason = "budget_snapshot_changed"
        if currentness_reason:
            service.supersede(
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                workflow_run_id=str(action_body["workflow_run_id"]),
                reason=currentness_reason,
                reservation_id=reservation.reservation_id,
                task_id=str(action_body["task_id"]),
                causation_id=causation_id or str(action_body.get("source_event_id") or ""),
                correlation_id=str(action_body["workflow_run_id"]),
            )
            _emit_fragment_once(
                writer,
                event_log.read_all(),
                "workflow.fragment.superseded",
                action_body,
                reason=currentness_reason,
                causation_id=causation_id,
                extra={
                    "operation_id": operation_id,
                    "reservation_id": reservation.reservation_id,
                },
            )
            return DynamicContinuationResult(
                "superseded",
                currentness_reason,
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                reservation_id=reservation.reservation_id,
                idempotency_key=reservation.idempotency_key,
            )

        admitted = _emit_fragment_once(
            writer,
            events,
            "workflow.fragment.admitted",
            action_body,
            reason="read_only_continuation_reserved",
            causation_id=causation_id,
            extra={
                "operation_id": operation_id,
                "request_hash": ensured.request_hash,
                "reservation_id": reservation.reservation_id,
                "idempotency_key": reservation.idempotency_key,
            },
        )
        prior_dispatch = _matching_dispatch_event(
            event_log.read_all(),
            operation_id,
        )
        if prior_dispatch is not None:
            return DynamicContinuationResult(
                "already_dispatched",
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                reservation_id=reservation.reservation_id,
                idempotency_key=reservation.idempotency_key,
                dispatch_event_id=prior_dispatch.id,
            )
        dispatch = writer.emit(
            "workflow.invoke.requested",
            actor="run-manager",
            task_id=str(action_body["task_id"]),
            causation_id=(admitted.id if admitted is not None else causation_id) or None,
            correlation_id=str(action_body["workflow_run_id"]),
            payload={
                "schema_version": "workflow-dynamic-continuation-dispatch.v1",
                "task_id": str(action_body["task_id"]),
                "pattern_id": str(action_body["pattern_id"]),
                "workflow_run_id": str(action_body["workflow_run_id"]),
                "run_id": str(
                    action_body.get("run_id")
                    or action_body["workflow_run_id"]
                ),
                "workflow_operation_id": operation_id,
                "workflow_operation_request_hash": ensured.request_hash,
                "parent_operation_id": str(action_body["parent_operation_id"]),
                "continuation_key": continuation_key,
                "expected_generation": generation,
                "task_map_generation": generation,
                "fragment_id": str(action_body["fragment_id"]),
                "fragment_digest": str(action_body["fragment_digest"]),
                "plan_artifact_package_id": str(
                    action_body["plan_artifact_package_id"],
                ),
                "plan_artifact_package_ref": str(
                    action_body["plan_artifact_package_ref"],
                ),
                "plan_artifact_package_digest": str(
                    action_body["plan_artifact_package_digest"],
                ),
                "trigger_checkpoint_ref": str(
                    action_body["trigger_checkpoint_ref"],
                ),
                "trigger_checkpoint_digest": str(
                    action_body["trigger_checkpoint_digest"],
                ),
                "provider_idempotency_key": reservation.idempotency_key,
                "reservation_id": reservation.reservation_id,
                "durable_operation": True,
                "result_protocol_mode": "blocking",
                "mode": "read_only",
                "expected_output": str(
                    action_body.get("expected_output")
                    or "typed read-only research result"
                ),
                "target_ref": str(action_body.get("target_ref") or ""),
                "artifact_refs": [{
                    "ref": str(action_body["trigger_checkpoint_ref"]),
                    "sha256": str(action_body["trigger_checkpoint_digest"]),
                }],
                "semantic_attempt_consumed": False,
            },
        )
        final_reason, _, _ = _currentness_preflight(
            state_dir,
            config=config,
            events=event_log.read_all(),
            action=action_body,
        )
        if final_reason:
            service.supersede(
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                workflow_run_id=str(action_body["workflow_run_id"]),
                reason=final_reason,
                reservation_id=reservation.reservation_id,
                task_id=str(action_body["task_id"]),
                causation_id=dispatch.id,
                correlation_id=str(action_body["workflow_run_id"]),
            )
            _emit_fragment_once(
                writer,
                event_log.read_all(),
                "workflow.fragment.superseded",
                action_body,
                reason=final_reason,
                causation_id=dispatch.id,
                extra={
                    "operation_id": operation_id,
                    "reservation_id": reservation.reservation_id,
                    "dispatch_event_id": dispatch.id,
                },
            )
            return DynamicContinuationResult(
                "superseded",
                final_reason,
                operation_id=operation_id,
                request_hash=ensured.request_hash,
                reservation_id=reservation.reservation_id,
                idempotency_key=reservation.idempotency_key,
                dispatch_event_id=dispatch.id,
            )
        return DynamicContinuationResult(
            "dispatched",
            operation_id=operation_id,
            request_hash=ensured.request_hash,
            reservation_id=reservation.reservation_id,
            idempotency_key=reservation.idempotency_key,
            dispatch_event_id=dispatch.id,
        )


def reconcile_reserved_read_only_continuations(
    state_dir: Path,
    *,
    event_log: EventLog,
    writer: EventWriter,
) -> int:
    """Recover accepted dispatches whose operation.started receipt was lost."""

    events = event_log.read_all()
    operations = reduce_workflow_operations(events)
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=writer,
    )
    reconciled = 0
    for operation_id, operation in operations.items():
        if str(operation.get("operation_type") or "") != "dynamic_read_only_workflow":
            continue
        if str(operation.get("status") or "") != "reserved":
            continue
        accepted = next((
            event
            for event in reversed(events)
            if event.type == "workflow.invoke.accepted"
            and str((event.payload or {}).get("workflow_operation_id") or "")
            == operation_id
        ), None)
        if accepted is None:
            continue
        payload = accepted.payload if isinstance(accepted.payload, dict) else {}
        started = service.mark_started(
            operation_id=operation_id,
            request_hash=str(operation.get("request_hash") or ""),
            workflow_run_id=str(operation.get("workflow_run_id") or ""),
            task_id=str(operation.get("task_id") or ""),
            dispatch_id=str(
                payload.get("fanout_request_event_id")
                or accepted.id
            ),
            reservation_id=str(operation.get("reservation_id") or ""),
            idempotency_key=str(operation.get("idempotency_key") or ""),
            causation_id=accepted.id,
            correlation_id=str(operation.get("workflow_run_id") or ""),
        )
        if started is not None:
            reconciled += 1
    return reconciled


__all__ = [
    "CONTINUATION_ENVELOPE_SCHEMA_VERSION",
    "DYNAMIC_CONTINUATION_ACTION",
    "DynamicContinuationResult",
    "FRAGMENT_SCHEMA_VERSION",
    "canonical_fragment_digest",
    "execute_read_only_continuation",
    "pending_read_only_continuation_actions",
    "reconcile_reserved_read_only_continuations",
]
