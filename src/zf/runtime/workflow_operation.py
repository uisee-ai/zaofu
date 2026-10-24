"""Replayable workflow-operation identity and event reducer.

Workflow operation state is derived exclusively from ``workflow.operation.*``
events.  Request and result bodies remain immutable sidecars; this module does
not create a writable operation journal.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import (
    CALL_RESULT_CANONICALIZATION,
    write_immutable_json_sidecar,
)
from zf.runtime.workflow_operation_task_pipeline import (
    admit_task_pipeline_redrive,
    apply_call_result_admission,
    apply_task_pipeline_operation_event,
    task_pipeline_operation_seed,
    task_pipeline_request_fields,
    task_pipeline_request_hash_body,
)


WORKFLOW_OPERATION_SCHEMA = "workflow-operation.v1"
WORKFLOW_OPERATION_CANONICALIZATION = "workflow-operation-request.v1"
OPERATION_EVENT_TYPES = frozenset({
    "workflow.operation.requested",
    "workflow.operation.reserved",
    "workflow.operation.started",
    "workflow.operation.retry_started",
    "workflow.operation.settled",
    "workflow.operation.failed",
    "workflow.operation.blocked",
    "workflow.operation.superseded",
    "workflow.operation.interrupted",
    "workflow.operation.redrive_admitted",
    "workflow.operation.cancelled",
})
TERMINAL_OPERATION_STATUSES = frozenset({
    "settled",
    "failed",
    "blocked",
    "superseded",
    "cancelled",
})

_VOLATILE_REQUEST_KEYS = frozenset({
    "attempt_id",
    "briefing_path",
    "completed_at",
    "created_at",
    "dispatch_id",
    "event_id",
    "last_event_id",
    "run_id",
    "started_at",
    "timestamp",
    "ts",
    "workdir",
})
class WorkflowOperationError(ValueError):
    """Stable operation replay invariant failed."""


@dataclass(frozen=True)
class EnsureOperationResult:
    status: str
    operation_id: str
    request_hash: str
    created: bool = False
    replay_hit: bool = False
    admitted_call_result_ref: str = ""
    admitted_call_result_digest: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ReserveOperationResult:
    status: str
    operation_id: str
    request_hash: str
    reservation_id: str = ""
    idempotency_key: str = ""
    created: bool = False
    replay_hit: bool = False
    reason: str = ""


def stable_operation_id(
    *,
    workflow_run_id: str,
    parent_stage_id: str,
    operation_key: str,
    operation_type: str = "agent",
) -> str:
    semantic = ":".join((workflow_run_id, parent_stage_id, operation_type, operation_key))
    digest = hashlib.sha256(semantic.encode("utf-8")).hexdigest()[:12]
    prefix = "-".join(
        _safe_component(item)[:32]
        for item in (parent_stage_id, operation_key)
        if str(item).strip()
    )[:72]
    return f"wop-{prefix or operation_type}-{digest}"


def stable_continuation_reservation_id(
    *,
    workflow_run_id: str,
    continuation_key: str,
    expected_generation: str,
) -> str:
    semantic = ":".join((
        workflow_run_id,
        continuation_key,
        expected_generation,
    ))
    return "wres-" + hashlib.sha256(semantic.encode("utf-8")).hexdigest()[:24]


def stable_continuation_idempotency_key(
    *,
    workflow_run_id: str,
    continuation_key: str,
    expected_generation: str,
) -> str:
    semantic = ":".join((
        "continuation-dispatch.v1",
        workflow_run_id,
        continuation_key,
        expected_generation,
    ))
    return "widem-" + hashlib.sha256(semantic.encode("utf-8")).hexdigest()


def canonicalize_operation_request(value: Any) -> Any:
    """Drop replay-volatile fields while preserving semantic request facts."""

    if isinstance(value, Mapping):
        return {
            str(key): canonicalize_operation_request(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _VOLATILE_REQUEST_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [canonicalize_operation_request(item) for item in value]
    if isinstance(value, set):
        normalized = [canonicalize_operation_request(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


def operation_request_hash(request: Mapping[str, Any]) -> str:
    normalized = {
        "canonicalization_version": WORKFLOW_OPERATION_CANONICALIZATION,
        "request": canonicalize_operation_request(request),
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reduce_workflow_operations(
    events: Iterable[ZfEvent],
    *,
    workflow_run_id: str = "",
    task_id: str = "",
) -> dict[str, dict[str, Any]]:
    """Deterministically rebuild operation views from archive+active events."""

    operations: dict[str, dict[str, Any]] = {}
    for event in events:
        if apply_call_result_admission(operations, event):
            continue
        if event.type not in OPERATION_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        operation_id = str(payload.get("operation_id") or "").strip()
        if not operation_id:
            continue
        event_run_id = str(payload.get("workflow_run_id") or "")
        event_task_id = str(event.task_id or payload.get("task_id") or "")
        if workflow_run_id and event_run_id != workflow_run_id:
            continue
        if task_id and event_task_id != task_id:
            continue
        request_hash = str(payload.get("request_hash") or "")
        row = operations.setdefault(operation_id, {
            "schema_version": WORKFLOW_OPERATION_SCHEMA,
            "workflow_run_id": event_run_id,
            "parent_operation_id": str(payload.get("parent_operation_id") or ""),
            "parent_stage_id": str(payload.get("parent_stage_id") or ""),
            "parent_attempt_id": str(payload.get("parent_attempt_id") or ""),
            "operation_id": operation_id,
            "operation_type": str(payload.get("operation_type") or "agent"),
            "attempt_domain": str(payload.get("attempt_domain") or ""),
            **task_pipeline_operation_seed(payload),
            "request_hash": request_hash,
            "request_ref": payload.get("request_ref") if isinstance(payload.get("request_ref"), dict) else {},
            "status": "requested",
            "task_id": event_task_id,
            "parent_task_id": str(payload.get("parent_task_id") or ""),
            "role_instance": str(payload.get("role_instance") or ""),
            "active_attempt_id": str(payload.get("active_attempt_id") or ""),
            "dispatch_id": str(payload.get("dispatch_id") or ""),
            "lease_id": str(payload.get("lease_id") or ""),
            "provider_session_id": str(payload.get("provider_session_id") or ""),
            "context_delivery_envelope_ref": {},
            "context_delivery_receipt_ref": {},
            "context_delivery_receipt_error": "",
            "child_task_ids": [],
            "admitted_call_result_ref": {},
            "provider_operation_summary_ref": {},
            "reservation_id": "",
            "reservation_expires_at": "",
            "continuation_key": "",
            "expected_generation": "",
            "expected_package_ref": "",
            "expected_package_digest": "",
            "pending_action_digest": "",
            "budget_snapshot": {},
            "started_at": "",
            "started_event_id": "",
            "idempotency_key": "",
            "source_event_ids": [],
            "request_count": 0,
            "replay_count": 0,
            "retry_count": 0,
            "retry_attempt": 0,
            "divergent": False,
            "reason": "",
        })
        if row["request_hash"] and request_hash and row["request_hash"] != request_hash:
            row["divergent"] = True
            row["status"] = "blocked"
            row["reason"] = "request_hash_divergence"
        elif request_hash and not row["request_hash"]:
            row["request_hash"] = request_hash
        row["source_event_ids"].append(event.id)
        children = payload.get("child_task_ids")
        if isinstance(children, list):
            row["child_task_ids"] = list(dict.fromkeys(
                [*row["child_task_ids"], *(str(item) for item in children if str(item).strip())]
            ))
        parent_task_id = str(payload.get("parent_task_id") or "")
        if parent_task_id:
            row["parent_task_id"] = parent_task_id
        row.update(task_pipeline_request_fields(payload))
        status_before_event = row["status"]
        if event.type == "workflow.operation.requested":
            row["request_count"] += 1
            row["replay_count"] = max(0, row["request_count"] - 1)
            if not row.get("request_ref") and isinstance(payload.get("request_ref"), dict):
                row["request_ref"] = dict(payload["request_ref"])
        elif (
            event.type == "workflow.operation.reserved"
            and row["status"] not in TERMINAL_OPERATION_STATUSES
        ):
            row["status"] = "reserved"
            for key in (
                "reservation_id",
                "reservation_expires_at",
                "continuation_key",
                "expected_generation",
                "expected_package_ref",
                "expected_package_digest",
                "pending_action_digest",
                "idempotency_key",
            ):
                value = str(payload.get(key) or "")
                if value:
                    row[key] = value
            budget_snapshot = payload.get("budget_snapshot")
            if isinstance(budget_snapshot, Mapping) and not row["started_at"]:
                row["budget_snapshot"] = dict(budget_snapshot)
        elif event.type == "workflow.operation.started" and row["status"] not in TERMINAL_OPERATION_STATUSES:
            first_start = not row["started_at"]
            row["status"] = "running"
            row["started_at"] = row["started_at"] or event.ts
            row["started_event_id"] = row["started_event_id"] or event.id
            for key in (
                "role_instance",
                "active_attempt_id",
                "dispatch_id",
                "lease_id",
                "provider_session_id",
            ):
                value = str(payload.get(key) or "")
                if value:
                    row[key] = value
            for key in (
                "context_delivery_envelope_ref",
                "context_delivery_receipt_ref",
            ):
                value = payload.get(key)
                if isinstance(value, Mapping):
                    row[key] = dict(value)
            row["context_delivery_receipt_error"] = str(
                payload.get("context_delivery_receipt_error") or ""
            )
            budget_snapshot = payload.get("budget_snapshot")
            # A same-operation redrive retains all prior usage. New attempts
            # and reservations do not grant a fresh operation allowance.
            if isinstance(budget_snapshot, Mapping) and first_start:
                row["budget_snapshot"] = dict(budget_snapshot)
            for key in ("reservation_id", "idempotency_key"):
                value = str(payload.get(key) or "")
                if value:
                    row[key] = value
        elif (
            event.type == "workflow.operation.retry_started"
            and row["status"] in {"suspended", "failed"}
            and str(payload.get("recovery_class") or "") == "transient_transport"
        ):
            row["status"] = "running"
            row["reason"] = ""
            row["retry_count"] += 1
            row["retry_attempt"] = int(payload.get("retry_attempt") or 0)
            for key in (
                "role_instance",
                "active_attempt_id",
                "dispatch_id",
                "lease_id",
            ):
                value = str(payload.get(key) or "")
                if value:
                    row[key] = value
        elif (
            event.type == "workflow.operation.settled"
            and row["status"] != "cancelled"
        ):
            row["status"] = "settled"
            result_ref = payload.get("admitted_call_result_ref")
            row["admitted_call_result_ref"] = dict(result_ref) if isinstance(result_ref, dict) else {}
            summary_ref = payload.get("provider_operation_summary_ref")
            row["provider_operation_summary_ref"] = (
                dict(summary_ref) if isinstance(summary_ref, dict) else {}
            )
            row["reason"] = str(payload.get("reason") or "")
        elif (
            event.type == "workflow.operation.failed"
            and row["status"] != "cancelled"
        ):
            row["status"] = "failed"
            row["reason"] = str(payload.get("reason") or "")
        elif (
            event.type == "workflow.operation.blocked"
            and row["status"] != "cancelled"
        ):
            row["status"] = "blocked"
            row["reason"] = str(payload.get("reason") or "")
        elif (
            event.type == "workflow.operation.superseded"
            and row["status"] != "cancelled"
        ):
            row["status"] = "superseded"
            row["reason"] = str(payload.get("reason") or "")
        elif (
            event.type == "workflow.operation.interrupted"
            and row["status"] not in TERMINAL_OPERATION_STATUSES
        ):
            row["status"] = "suspended"
            row["reason"] = str(payload.get("reason") or "")
        elif apply_task_pipeline_operation_event(row, event, payload):
            pass
        elif (
            event.type == "workflow.operation.cancelled"
            and row["status"] not in TERMINAL_OPERATION_STATUSES
        ):
            row["status"] = "cancelled"
            row["reason"] = str(payload.get("reason") or "")
        ignored_terminal_race = (
            status_before_event == "cancelled"
            and event.type in {
                "workflow.operation.settled",
                "workflow.operation.failed",
                "workflow.operation.blocked",
                "workflow.operation.superseded",
            }
        ) or (
            event.type == "workflow.operation.cancelled"
            and status_before_event in TERMINAL_OPERATION_STATUSES
        )
        if not ignored_terminal_race:
            row["last_event_id"] = event.id
            row["last_event_type"] = event.type
            row["last_event_at"] = event.ts
    return operations


def load_workflow_operation(
    event_log: EventLog,
    operation_id: str,
) -> dict[str, Any] | None:
    return reduce_workflow_operations(event_log.read_all()).get(operation_id)


class WorkflowOperationService:
    def __init__(
        self,
        *,
        state_dir: Path,
        event_log: EventLog,
        event_writer: EventWriter,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.event_log = event_log
        self.event_writer = event_writer

    def ensure_operation(
        self,
        *,
        workflow_run_id: str,
        operation_id: str,
        operation_type: str,
        request: Mapping[str, Any],
        parent_operation_id: str = "",
        parent_stage_id: str = "",
        parent_attempt_id: str = "",
        task_id: str = "",
        parent_task_id: str = "",
        role_instance: str = "",
        active_attempt_id: str = "",
        lease_id: str = "",
        child_task_ids: list[str] | None = None,
        causation_id: str = "",
        correlation_id: str = "",
    ) -> EnsureOperationResult:
        request_body = {
            "schema_version": "workflow-operation-request.v1",
            "canonicalization_version": WORKFLOW_OPERATION_CANONICALIZATION,
            "workflow_run_id": workflow_run_id,
            "operation_id": operation_id,
            "operation_type": operation_type,
            "attempt_domain": str(request.get("attempt_domain") or ""),
            "parent_operation_id": parent_operation_id,
            "parent_stage_id": parent_stage_id,
            "parent_attempt_id": parent_attempt_id,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "role_instance": role_instance,
            "active_attempt_id": active_attempt_id,
            "lease_id": lease_id,
            "child_task_ids": list(child_task_ids or []),
            "request": canonicalize_operation_request(request),
        }
        request_hash = operation_request_hash(
            task_pipeline_request_hash_body(request_body, request)
        )
        lock_path = self.state_dir / "projections" / "workflow-operations" / f"{_safe_component(operation_id)}.guard"
        with locked_path(lock_path):
            existing = load_workflow_operation(self.event_log, operation_id)
            if existing is not None:
                existing_hash = str(existing.get("request_hash") or "")
                if existing_hash and existing_hash != request_hash:
                    compatibility = self._task_pipeline_replay_compatibility(
                        existing=existing,
                        current_request_body=request_body,
                        current_request_hash=request_hash,
                    )
                    if compatibility is not None:
                        compatibility_digest, compatibility_request_ref = compatibility
                        status = str(existing.get("status") or "requested")
                        if (
                            status == "blocked"
                            and str(existing.get("reason") or "")
                            in {
                                "request_hash_divergence",
                                "request_hash_compatibility_failed",
                            }
                        ):
                            source_attempt_ids = list(
                                existing.get("redrive_source_attempt_ids") or []
                            )
                            admit_task_pipeline_redrive(
                                self,
                                operation_id=operation_id,
                                request_hash=existing_hash,
                                workflow_run_id=workflow_run_id,
                                task_id=task_id,
                                source_attempt_id=(
                                    str(source_attempt_ids[-1])
                                    if source_attempt_ids
                                    else ""
                                ),
                                recovery_decision_event_id=str(
                                    existing.get("last_event_id") or causation_id
                                ),
                                reason=(
                                    "Task Pipeline replay request differs only "
                                    "by attempt-local input evidence"
                                ),
                                recovery_decision_owner="kernel_replay",
                                compatibility_proof_digest=compatibility_digest,
                                compatibility_request_ref=compatibility_request_ref,
                            )
                            status = "requested"
                        result_ref = existing.get("admitted_call_result_ref")
                        result_ref = (
                            result_ref if isinstance(result_ref, dict) else {}
                        )
                        return EnsureOperationResult(
                            status=status,
                            operation_id=operation_id,
                            request_hash=existing_hash,
                            replay_hit=True,
                            admitted_call_result_ref=str(
                                result_ref.get("ref") or ""
                            ),
                            admitted_call_result_digest=str(
                                result_ref.get("sha256") or ""
                            ),
                            reason="task_pipeline_attempt_local_replay",
                        )
                    if (
                        str(existing.get("status") or "") == "blocked"
                        and str(existing.get("reason") or "")
                        == "request_hash_divergence"
                    ):
                        self._emit_once(
                            "workflow.operation.blocked",
                            operation_id=operation_id,
                            request_hash=existing_hash,
                            workflow_run_id=workflow_run_id,
                            task_id=task_id,
                            payload={
                                "reason": "request_hash_compatibility_failed",
                                "expected_request_hash": existing_hash,
                                "actual_request_hash": request_hash,
                            },
                            causation_id=causation_id,
                            correlation_id=correlation_id,
                        )
                        return EnsureOperationResult(
                            status="divergent",
                            operation_id=operation_id,
                            request_hash=request_hash,
                            reason="request_hash_compatibility_failed",
                        )
                    self._emit_once(
                        "workflow.operation.blocked",
                        operation_id=operation_id,
                        request_hash=request_hash,
                        workflow_run_id=workflow_run_id,
                        task_id=task_id,
                        payload={
                            "reason": "request_hash_divergence",
                            "expected_request_hash": existing_hash,
                            "actual_request_hash": request_hash,
                        },
                        causation_id=causation_id,
                        correlation_id=correlation_id,
                    )
                    return EnsureOperationResult(
                        status="divergent",
                        operation_id=operation_id,
                        request_hash=request_hash,
                        reason="request_hash_divergence",
                    )
                status = str(existing.get("status") or "requested")
                result_ref = existing.get("admitted_call_result_ref")
                result_ref = result_ref if isinstance(result_ref, dict) else {}
                return EnsureOperationResult(
                    status=status,
                    operation_id=operation_id,
                    request_hash=request_hash,
                    replay_hit=True,
                    admitted_call_result_ref=str(result_ref.get("ref") or ""),
                    admitted_call_result_digest=str(result_ref.get("sha256") or ""),
                )
            request_descriptor = write_immutable_json_sidecar(
                self.state_dir,
                request_body,
                root="operations/requests",
                kind="workflow_operation_request",
                schema_version="workflow-operation-request.v1",
                created_by="workflow-operation-service",
                source_event_id=causation_id,
            )
            self.event_writer.append(ZfEvent(
                type="workflow.operation.requested",
                actor="zf-cli",
                origin="kernel",
                task_id=task_id or None,
                payload={
                    "schema_version": WORKFLOW_OPERATION_SCHEMA,
                    "canonicalization_version": WORKFLOW_OPERATION_CANONICALIZATION,
                    "call_result_canonicalization_version": CALL_RESULT_CANONICALIZATION,
                    "workflow_run_id": workflow_run_id,
                    "parent_operation_id": parent_operation_id,
                    "parent_stage_id": parent_stage_id,
                    "parent_attempt_id": parent_attempt_id,
                    "operation_id": operation_id,
                    "operation_type": operation_type,
                    "attempt_domain": str(request.get("attempt_domain") or ""),
                    "request_hash": request_hash,
                    "request_ref": request_descriptor,
                    "task_id": task_id,
                    "parent_task_id": parent_task_id,
                    "role_instance": role_instance,
                    "active_attempt_id": active_attempt_id,
                    "lease_id": lease_id,
                    "child_task_ids": list(child_task_ids or []),
                    **task_pipeline_request_fields(request),
                },
                causation_id=causation_id or None,
                correlation_id=correlation_id or workflow_run_id or None,
            ))
            return EnsureOperationResult(
                status="requested",
                operation_id=operation_id,
                request_hash=request_hash,
                created=True,
            )

    def _task_pipeline_replay_compatibility(
        self,
        *,
        existing: Mapping[str, Any],
        current_request_body: Mapping[str, Any],
        current_request_hash: str,
    ) -> tuple[str, dict[str, Any]] | None:
        """Prove an old Task Pipeline request differs only by attempt evidence."""

        descriptor = existing.get("request_ref")
        if not isinstance(descriptor, dict) or not descriptor:
            return None
        try:
            from zf.runtime.sidecar_refs import hydrate_sidecar_ref

            hydrated = hydrate_sidecar_ref(
                self.state_dir,
                descriptor,
                purpose="task_pipeline_request_replay_compatibility",
                actor="workflow-operation-service",
            )
        except Exception:
            return None
        persisted = hydrated.payload
        if not isinstance(persisted, Mapping):
            return None
        persisted_request = persisted.get("request")
        current_request = current_request_body.get("request")
        if not isinstance(persisted_request, Mapping) or not isinstance(
            current_request, Mapping
        ):
            return None
        if not str(current_request.get("task_pipeline_stage") or "").strip():
            return None
        persisted_compatibility_hash = operation_request_hash(
            task_pipeline_request_hash_body(persisted, persisted_request)
        )
        current_compatibility_hash = operation_request_hash(
            task_pipeline_request_hash_body(
                current_request_body,
                current_request,
            )
        )
        if (
            persisted_compatibility_hash != current_compatibility_hash
            or current_compatibility_hash != current_request_hash
        ):
            return None
        compatibility_body = {
            "schema_version": "task-pipeline-request-replay-compatibility.v1",
            "operation_id": str(existing.get("operation_id") or ""),
            "authoritative_request_hash": str(existing.get("request_hash") or ""),
            "compatibility_request_hash": current_compatibility_hash,
            "persisted_request_ref": dict(descriptor),
            "ignored_attempt_local_fields": [
                "source_manifest_digest",
                "read_policy_digest",
                "execution_profile.role",
            ],
            "current_request": dict(current_request_body),
        }
        compatibility_descriptor = write_immutable_json_sidecar(
            self.state_dir,
            compatibility_body,
            root="operations/replay-compatibility",
            kind="task_pipeline_request_replay_compatibility",
            schema_version=(
                "task-pipeline-request-replay-compatibility.v1"
            ),
            created_by="workflow-operation-service",
            source_event_id=str(existing.get("last_event_id") or ""),
        )
        return (
            str(compatibility_descriptor.get("sha256") or ""),
            compatibility_descriptor,
        )

    def reserve_continuation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        continuation_key: str,
        expected_generation: str,
        expected_package_ref: str,
        expected_package_digest: str,
        pending_action_digest: str,
        budget_snapshot: Mapping[str, Any],
        reservation_expires_at: str,
        parent_operation_id: str = "",
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
    ) -> ReserveOperationResult:
        reservation_id = stable_continuation_reservation_id(
            workflow_run_id=workflow_run_id,
            continuation_key=continuation_key,
            expected_generation=expected_generation,
        )
        idempotency_key = stable_continuation_idempotency_key(
            workflow_run_id=workflow_run_id,
            continuation_key=continuation_key,
            expected_generation=expected_generation,
        )
        lock_path = (
            self.state_dir
            / "projections"
            / "workflow-operations"
            / f"{_safe_component(operation_id)}.guard"
        )
        with locked_path(lock_path):
            existing = load_workflow_operation(self.event_log, operation_id)
            if existing is None:
                return ReserveOperationResult(
                    status="missing",
                    operation_id=operation_id,
                    request_hash=request_hash,
                    reason="workflow_operation_missing",
                )
            if str(existing.get("request_hash") or "") != request_hash:
                return ReserveOperationResult(
                    status="divergent",
                    operation_id=operation_id,
                    request_hash=request_hash,
                    reason="request_hash_divergence",
                )
            status = str(existing.get("status") or "requested")
            if status in {"reserved", "running"} | TERMINAL_OPERATION_STATUSES:
                existing_reservation = str(existing.get("reservation_id") or "")
                if existing_reservation and existing_reservation != reservation_id:
                    return ReserveOperationResult(
                        status="divergent",
                        operation_id=operation_id,
                        request_hash=request_hash,
                        reason="reservation_identity_divergence",
                    )
                return ReserveOperationResult(
                    status=status,
                    operation_id=operation_id,
                    request_hash=request_hash,
                    reservation_id=existing_reservation or reservation_id,
                    idempotency_key=(
                        str(existing.get("idempotency_key") or "")
                        or idempotency_key
                    ),
                    replay_hit=True,
                    reason=str(existing.get("reason") or ""),
                )
            self._emit_once(
                "workflow.operation.reserved",
                operation_id=operation_id,
                request_hash=request_hash,
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                payload={
                    "reservation_id": reservation_id,
                    "reservation_expires_at": reservation_expires_at,
                    "continuation_key": continuation_key,
                    "expected_generation": expected_generation,
                    "expected_package_ref": expected_package_ref,
                    "expected_package_digest": expected_package_digest,
                    "parent_operation_id": parent_operation_id,
                    "pending_action_digest": pending_action_digest,
                    "budget_snapshot": dict(budget_snapshot),
                    "idempotency_key": idempotency_key,
                    "semantic_attempt_consumed": False,
                },
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            return ReserveOperationResult(
                status="reserved",
                operation_id=operation_id,
                request_hash=request_hash,
                reservation_id=reservation_id,
                idempotency_key=idempotency_key,
                created=True,
            )

    def mark_started(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        task_id: str = "",
        dispatch_id: str = "",
        role_instance: str = "",
        active_attempt_id: str = "",
        lease_id: str = "",
        provider_session_id: str = "",
        context_delivery_envelope_ref: Mapping[str, Any] | None = None,
        context_delivery_receipt_ref: Mapping[str, Any] | None = None,
        context_delivery_receipt_error: str = "",
        budget_snapshot: Mapping[str, Any] | None = None,
        reservation_id: str = "",
        idempotency_key: str = "",
        causation_id: str = "",
        correlation_id: str = "",
    ) -> ZfEvent | None:
        return self._emit_once(
            "workflow.operation.started",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={
                "dispatch_id": dispatch_id,
                "role_instance": role_instance,
                "active_attempt_id": active_attempt_id,
                "lease_id": lease_id,
                "provider_session_id": provider_session_id,
                "context_delivery_envelope_ref": dict(
                    context_delivery_envelope_ref or {}
                ),
                "context_delivery_receipt_ref": dict(
                    context_delivery_receipt_ref or {}
                ),
                "context_delivery_receipt_error": str(
                    context_delivery_receipt_error or ""
                )[:512],
                "budget_snapshot": dict(budget_snapshot or {}),
                "reservation_id": reservation_id,
                "idempotency_key": idempotency_key,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def supersede(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        reason: str,
        reservation_id: str = "",
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
    ) -> ZfEvent | None:
        return self._emit_once(
            "workflow.operation.superseded",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={
                "reason": reason,
                "reservation_id": reservation_id,
                "semantic_attempt_consumed": False,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def mark_retry_started(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        retry_attempt: int,
        reason: str,
        dispatch_id: str = "",
        role_instance: str = "",
        active_attempt_id: str = "",
        lease_id: str = "",
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
    ) -> ZfEvent | None:
        if retry_attempt != 1:
            raise WorkflowOperationError(
                "workflow operation transport retry must be exactly attempt 1"
            )
        current = load_workflow_operation(self.event_log, operation_id)
        if current is None or str(current.get("request_hash") or "") != request_hash:
            raise WorkflowOperationError("workflow operation retry identity mismatch")
        if str(current.get("status") or "") not in {"suspended", "failed"}:
            raise WorkflowOperationError(
                "workflow operation retry requires suspended or failed status"
            )
        return self._emit_once(
            "workflow.operation.retry_started",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={
                "retry_attempt": retry_attempt,
                "recovery_class": "transient_transport",
                "reason": reason,
                "dispatch_id": dispatch_id,
                "role_instance": role_instance,
                "active_attempt_id": active_attempt_id,
                "lease_id": lease_id,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def settle(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        admitted_call_result_ref: Mapping[str, Any],
        provider_operation_summary_ref: Mapping[str, Any] | None = None,
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
    ) -> ZfEvent | None:
        if not str(admitted_call_result_ref.get("ref") or "") or not str(
            admitted_call_result_ref.get("sha256") or ""
        ):
            raise WorkflowOperationError("settled operation requires admitted call-result ref")
        return self._emit_once(
            "workflow.operation.settled",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={
                "admitted_call_result_ref": dict(admitted_call_result_ref),
                "provider_operation_summary_ref": dict(
                    provider_operation_summary_ref or {}
                ),
                "reason": "admitted_call_result",
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def cancel(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        reason: str,
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
    ) -> ZfEvent | None:
        return self._emit_once(
            "workflow.operation.cancelled",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={"reason": reason},
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def block(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        reason: str,
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> ZfEvent | None:
        return self._emit_once(
            "workflow.operation.blocked",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={"reason": reason, **dict(details or {})},
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def interrupt(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        reason: str,
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
        source_attempt_id: str = "",
    ) -> ZfEvent | None:
        return self._emit_once(
            "workflow.operation.interrupted",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={
                "reason": reason,
                "source_attempt_id": source_attempt_id,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def admit_redrive(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        task_id: str,
        source_attempt_id: str,
        recovery_decision_event_id: str,
        reason: str,
        recovery_decision_owner: str = "run_manager",
        compatibility_proof_digest: str = "",
        compatibility_request_ref: Mapping[str, Any] | None = None,
    ) -> ZfEvent | None:
        """Admit one Run Manager decision for same-operation redrive."""
        return admit_task_pipeline_redrive(
            self,
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            source_attempt_id=source_attempt_id,
            recovery_decision_event_id=recovery_decision_event_id,
            reason=reason,
            recovery_decision_owner=recovery_decision_owner,
            compatibility_proof_digest=compatibility_proof_digest,
            compatibility_request_ref=compatibility_request_ref,
        )

    def fail(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        reason: str,
        task_id: str = "",
        causation_id: str = "",
        correlation_id: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> ZfEvent | None:
        return self._emit_once(
            "workflow.operation.failed",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={"reason": reason, **dict(details or {})},
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def _emit_once(
        self,
        event_type: str,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        task_id: str,
        payload: Mapping[str, Any],
        causation_id: str,
        correlation_id: str,
    ) -> ZfEvent | None:
        events = self.event_log.read_all()
        existing_operation = reduce_workflow_operations(events).get(operation_id, {})
        effective_task_id = str(
            task_id or existing_operation.get("task_id") or ""
        )
        parent_task_id = str(existing_operation.get("parent_task_id") or "")
        emitted_body = dict(payload)
        if parent_task_id:
            emitted_body.setdefault("parent_task_id", parent_task_id)
        for event in reversed(events):
            if event.type != event_type:
                continue
            existing_body = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(existing_body.get("operation_id") or "") == operation_id
                and str(existing_body.get("request_hash") or "") == request_hash
            ):
                if event_type == "workflow.operation.started":
                    occurrence = (
                        str(payload.get("active_attempt_id") or ""),
                        str(payload.get("dispatch_id") or ""),
                    )
                    existing_occurrence = (
                        str(existing_body.get("active_attempt_id") or ""),
                        str(existing_body.get("dispatch_id") or ""),
                    )
                    if occurrence != existing_occurrence:
                        continue
                if (
                    event_type == "workflow.operation.interrupted"
                    and causation_id
                    and str(event.causation_id or "") != causation_id
                ):
                    continue
                source_attempt_id = str(payload.get("source_attempt_id") or "")
                if not source_attempt_id or str(
                    existing_body.get("source_attempt_id") or ""
                ) == source_attempt_id:
                    return None
        return self.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            origin="kernel",
            task_id=effective_task_id or None,
            payload={
                "schema_version": WORKFLOW_OPERATION_SCHEMA,
                "workflow_run_id": workflow_run_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "task_id": effective_task_id,
                **emitted_body,
            },
            causation_id=causation_id or None,
            correlation_id=correlation_id or workflow_run_id or None,
        ))


def interrupt_active_workflow_operations(
    *,
    state_dir: Path,
    event_log: EventLog,
    event_writer: EventWriter,
    reason: str,
    causation_id: str = "",
) -> list[ZfEvent]:
    """Suspend every non-terminal operation before worker processes stop."""

    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=event_log,
        event_writer=event_writer,
    )
    interrupted: list[ZfEvent] = []
    for operation in reduce_workflow_operations(event_log.read_all()).values():
        if str(operation.get("status") or "") not in {
            "requested",
            "reserved",
            "running",
        }:
            continue
        event = service.interrupt(
            operation_id=str(operation.get("operation_id") or ""),
            request_hash=str(operation.get("request_hash") or ""),
            workflow_run_id=str(operation.get("workflow_run_id") or ""),
            task_id=str(operation.get("task_id") or ""),
            reason=reason,
            source_attempt_id=str(operation.get("active_attempt_id") or ""),
            causation_id=causation_id,
            correlation_id=str(operation.get("workflow_run_id") or ""),
        )
        if event is not None:
            interrupted.append(event)
    return interrupted


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._") or "operation"


__all__ = [
    "OPERATION_EVENT_TYPES",
    "TERMINAL_OPERATION_STATUSES",
    "WORKFLOW_OPERATION_CANONICALIZATION",
    "WORKFLOW_OPERATION_SCHEMA",
    "EnsureOperationResult",
    "ReserveOperationResult",
    "WorkflowOperationError",
    "WorkflowOperationService",
    "canonicalize_operation_request",
    "interrupt_active_workflow_operations",
    "load_workflow_operation",
    "operation_request_hash",
    "reduce_workflow_operations",
    "stable_continuation_idempotency_key",
    "stable_continuation_reservation_id",
    "stable_operation_id",
]
