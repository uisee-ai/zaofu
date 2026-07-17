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


WORKFLOW_OPERATION_SCHEMA = "workflow-operation.v1"
WORKFLOW_OPERATION_CANONICALIZATION = "workflow-operation-request.v1"
OPERATION_EVENT_TYPES = frozenset({
    "workflow.operation.requested",
    "workflow.operation.started",
    "workflow.operation.settled",
    "workflow.operation.failed",
    "workflow.operation.blocked",
})
TERMINAL_OPERATION_STATUSES = frozenset({"settled", "failed", "blocked"})

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
            "request_hash": request_hash,
            "request_ref": payload.get("request_ref") if isinstance(payload.get("request_ref"), dict) else {},
            "status": "requested",
            "task_id": event_task_id,
            "child_task_ids": [],
            "admitted_call_result_ref": {},
            "source_event_ids": [],
            "request_count": 0,
            "replay_count": 0,
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
        if event.type == "workflow.operation.requested":
            row["request_count"] += 1
            row["replay_count"] = max(0, row["request_count"] - 1)
            if not row.get("request_ref") and isinstance(payload.get("request_ref"), dict):
                row["request_ref"] = dict(payload["request_ref"])
        elif event.type == "workflow.operation.started" and row["status"] not in TERMINAL_OPERATION_STATUSES:
            row["status"] = "running"
        elif event.type == "workflow.operation.settled":
            row["status"] = "settled"
            result_ref = payload.get("admitted_call_result_ref")
            row["admitted_call_result_ref"] = dict(result_ref) if isinstance(result_ref, dict) else {}
            row["reason"] = str(payload.get("reason") or "")
        elif event.type == "workflow.operation.failed":
            row["status"] = "failed"
            row["reason"] = str(payload.get("reason") or "")
        elif event.type == "workflow.operation.blocked":
            row["status"] = "blocked"
            row["reason"] = str(payload.get("reason") or "")
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
            "parent_operation_id": parent_operation_id,
            "parent_stage_id": parent_stage_id,
            "parent_attempt_id": parent_attempt_id,
            "task_id": task_id,
            "child_task_ids": list(child_task_ids or []),
            "request": canonicalize_operation_request(request),
        }
        request_hash = operation_request_hash(request_body)
        lock_path = self.state_dir / "projections" / "workflow-operations" / f"{_safe_component(operation_id)}.guard"
        with locked_path(lock_path):
            existing = load_workflow_operation(self.event_log, operation_id)
            if existing is not None:
                existing_hash = str(existing.get("request_hash") or "")
                if existing_hash and existing_hash != request_hash:
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
                    "request_hash": request_hash,
                    "request_ref": request_descriptor,
                    "task_id": task_id,
                    "child_task_ids": list(child_task_ids or []),
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

    def mark_started(
        self,
        *,
        operation_id: str,
        request_hash: str,
        workflow_run_id: str,
        task_id: str = "",
        dispatch_id: str = "",
        provider_session_id: str = "",
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
                "provider_session_id": provider_session_id,
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
                "reason": "admitted_call_result",
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
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
    ) -> ZfEvent | None:
        return self._emit_once(
            "workflow.operation.failed",
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            payload={"reason": reason},
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
        for event in reversed(self.event_log.read_all()):
            if event.type != event_type:
                continue
            body = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(body.get("operation_id") or "") == operation_id
                and str(body.get("request_hash") or "") == request_hash
            ):
                return None
        return self.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "schema_version": WORKFLOW_OPERATION_SCHEMA,
                "workflow_run_id": workflow_run_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "task_id": task_id,
                **dict(payload),
            },
            causation_id=causation_id or None,
            correlation_id=correlation_id or workflow_run_id or None,
        ))


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._") or "operation"


__all__ = [
    "OPERATION_EVENT_TYPES",
    "TERMINAL_OPERATION_STATUSES",
    "WORKFLOW_OPERATION_CANONICALIZATION",
    "WORKFLOW_OPERATION_SCHEMA",
    "EnsureOperationResult",
    "WorkflowOperationError",
    "WorkflowOperationService",
    "canonicalize_operation_request",
    "load_workflow_operation",
    "operation_request_hash",
    "reduce_workflow_operations",
    "stable_operation_id",
]
