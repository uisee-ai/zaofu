"""Nonblocking, replayable read-only reflection on WorkflowOperation.

The provider lock represents a live call, not canonical task state. Requests,
results and completion are durable sidecars/events; the next tick repairs a
handoff interrupted by process exit. No model call holds the RM tick lock.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from threading import Thread
from typing import Any, Callable

from zf.core.events.writer import EventWriter
from zf.core.state.locks import FileLock, locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.pause_lifecycle import active_request_age_seconds, is_dispatch_paused
from zf.runtime.run_scope import events_for_run
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations

REQUESTED = "run.manager.reflect.requested"
COMPLETED = "run.manager.reflect.completed"
OPERATION_TYPE = "run_manager_reflect"


def submit_reflection(
    *, state_dir: Path, writer: EventWriter, request_id: str, prompt: str,
    metadata: dict[str, Any], timeout_seconds: int, invoke: Callable[[str], dict[str, Any]],
) -> bool:
    service = WorkflowOperationService(
        state_dir=state_dir, event_log=writer.event_log, event_writer=writer,
    )
    operation_id = "wop-" + request_id
    existing = reduce_workflow_operations(writer.event_log.read_all()).get(operation_id)
    if existing and existing["status"] in {"settled", "failed", "cancelled", "superseded"}:
        return False
    if existing is None:
        service.ensure_operation(
            workflow_run_id=str(metadata.get("workflow_run_id") or ""),
            operation_id=operation_id, operation_type=OPERATION_TYPE,
            task_id=str(metadata.get("task_id") or ""), role_instance="run-manager",
            causation_id=str(metadata.get("source_event_id") or ""),
            request={"prompt": prompt, "request_id": request_id,
                     "metadata": metadata, "timeout_seconds": timeout_seconds},
        )
    reconcile_reflections(state_dir=state_dir, writer=writer, invoke=invoke)
    return existing is None


def reconcile_reflections(
    *, state_dir: Path, writer: EventWriter, invoke: Callable[[str], dict[str, Any]],
    now: datetime | None = None,
    dispatch_enabled: bool = True,
) -> None:
    current = now or datetime.now(timezone.utc)
    events = writer.event_log.read_all()
    completed = {e.payload.get("request_id") for e in events if e.type == COMPLETED}
    requested = {e.payload.get("request_id"): e for e in events if e.type == REQUESTED}
    service = WorkflowOperationService(
        state_dir=state_dir, event_log=writer.event_log, event_writer=writer,
    )
    for operation in reduce_workflow_operations(events).values():
        if operation.get("operation_type") != OPERATION_TYPE:
            continue
        try:
            body = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload
            request = body["request"]
        except (KeyError, OSError, ValueError):
            # A required input cannot be fabricated from the current chat.
            fallback = next((e for e in requested.values()
                             if e.payload.get("operation_id") == operation["operation_id"]), None)
            request_id = (fallback.payload.get("request_id") if fallback else
                          operation["operation_id"].removeprefix("wop-"))
            with locked_path(_result_lock_path(state_dir, operation)):
                latest_events = writer.event_log.read_all()
                latest = reduce_workflow_operations(latest_events)[operation["operation_id"]]
                if latest.get("status") not in {"settled", "failed", "cancelled", "superseded"}:
                    service.fail(**_identity(operation), reason="reflection_request_unreadable")
                if not any(e.type == COMPLETED and e.payload.get("request_id") == request_id for e in latest_events):
                    writer.emit(COMPLETED, actor="run-manager", payload={
                        "schema_version": "run-manager.reflect.v2", "request_id": request_id,
                        "operation_id": operation["operation_id"], "status": "failed",
                        "reason": "reflection_request_unreadable", "verdict": "unknown",
                        "recommended_route": "autoresearch", "apply_policy": "proposal_only",
                    })
            continue
        request_id = request["request_id"]
        if request_id in completed:
            continue
        if operation.get("status") in {"cancelled", "superseded"}:
            _publish_completion(state_dir, writer, operation, request)
            continue
        if operation.get("status") == "blocked":
            continue
        if operation.get("status") in {"settled", "failed"}:
            _publish_completion(state_dir, writer, operation, request)
            continue
        if not dispatch_enabled:
            service.cancel(**_identity(operation), reason="reflection_disabled")
            _publish_completion(state_dir, writer, {
                **operation, "status": "cancelled", "reason": "reflection_disabled",
            }, request)
            continue
        scoped = events_for_run(events, run_id=operation.get("workflow_run_id", ""))
        cancelled = False
        for event in scoped:
            if event.type == "run.cancelled":
                cancelled = True
            elif event.type == "run.goal.started" or (
                event.type == "run.goal.updated" and event.payload.get("status") in {"active", "running"}
            ):
                cancelled = False
        if cancelled:
            service.cancel(**_identity(operation), reason="reflection_run_cancelled")
            _publish_completion(state_dir, writer, {**operation, "status": "cancelled"}, request)
            continue
        if is_dispatch_paused(state_dir, events=events):
            continue
        start = requested.get(request_id)
        if start is None:
            start = writer.emit(REQUESTED, actor="run-manager",
                task_id=operation.get("task_id") or None,
                causation_id=request["metadata"].get("source_event_id") or None,
                correlation_id=request_id, payload={
                "schema_version": "run-manager.reflect.v2", **request["metadata"],
                "request_id": request_id, "operation_id": operation["operation_id"],
                "request_ref": operation["request_ref"], "apply_policy": "proposal_only",
            })
        started_at = datetime.fromisoformat(start.ts.replace("Z", "+00:00"))
        if active_request_age_seconds(events, created_at=started_at, now=current) >= request["timeout_seconds"]:
            _finish(state_dir, writer, operation, request, {
                "status": "timeout", "verdict": "unknown", "recommended_route": "autoresearch",
                "reason": "reflection response deadline exceeded", "apply_policy": "proposal_only",
            })
            continue
        lock = FileLock(state_dir / "locks" / "run-manager-reflect-provider.lock", timeout_seconds=0)
        try:
            lock.__enter__()
        except TimeoutError:
            continue
        try:
            service.mark_started(**_identity(operation), role_instance="run-manager")
        except Exception:
            lock.__exit__(None, None, None)
            raise

        def run(op=operation, req=request, held=lock):
            try:
                try:
                    result = invoke(req["prompt"])
                except Exception as exc:
                    result = {"status": "failed", "verdict": "unknown",
                              "recommended_route": "autoresearch", "reason": str(exc)[:500]}
                _finish(state_dir, writer, op, req, result)
            finally:
                held.__exit__(None, None, None)

        try:
            Thread(target=run, name="zf-run-manager-reflect", daemon=True).start()
        except Exception:
            lock.__exit__(None, None, None)
            raise


def _identity(operation: dict[str, Any]) -> dict[str, str]:
    return {key: str(operation.get(key) or "") for key in (
        "operation_id", "request_hash", "workflow_run_id", "task_id",
    )}


def _finish(
    state_dir: Path, writer: EventWriter, operation: dict[str, Any],
    request: dict[str, Any], result: dict[str, Any],
) -> None:
    # Timeout and late provider response compete only on this short commit.
    with locked_path(_result_lock_path(state_dir, operation)):
        current = reduce_workflow_operations(writer.event_log.read_all()).get(operation["operation_id"], {})
        if current.get("status") in {"settled", "failed", "cancelled", "superseded", "blocked"}:
            return
        ref = write_immutable_json_sidecar(
            state_dir, {**result, "apply_policy": "proposal_only"},
            root="operations/results", kind="run_manager_reflection",
            schema_version="run-manager.reflect-result.v1", created_by="run-manager",
        )
        service = WorkflowOperationService(
            state_dir=state_dir, event_log=writer.event_log, event_writer=writer,
        )
        service.settle(**_identity(operation), admitted_call_result_ref=ref)
    _publish_completion(state_dir, writer, operation, request)


def _result_lock_path(state_dir: Path, operation: dict[str, Any]) -> Path:
    lock_key = hashlib.sha256(operation["operation_id"].encode("utf-8")).hexdigest()
    return state_dir / "locks" / ("reflect-result-" + lock_key)


def _publish_completion(
    state_dir: Path, writer: EventWriter, operation: dict[str, Any], request: dict[str, Any],
) -> None:
    with locked_path(_result_lock_path(state_dir, operation)):
        current = reduce_workflow_operations(writer.event_log.read_all()).get(operation["operation_id"], operation)
        _publish_completion_unlocked(state_dir, writer, current, request)


def _publish_completion_unlocked(
    state_dir: Path, writer: EventWriter, operation: dict[str, Any], request: dict[str, Any],
) -> None:
    if any(e.type == COMPLETED and e.payload.get("request_id") == request["request_id"]
           for e in writer.event_log.read_all()):
        return
    ref = operation.get("admitted_call_result_ref")
    try:
        result = (hydrate_sidecar_ref(state_dir, ref).payload if ref else
                  {"status": "failed", "verdict": "unknown", "recommended_route": "autoresearch",
                   "reason": operation.get("reason", "reflection_failed")})
    except (OSError, ValueError, KeyError):
        result = {"status": "failed", "verdict": "unknown", "recommended_route": "autoresearch",
                  "reason": "reflection_result_unreadable"}
    if operation.get("status") in {"cancelled", "superseded"}:
        result = {"status": operation["status"], "verdict": "unknown", "recommended_route": "wait",
                  "reason": operation.get("reason", "reflection_cancelled")}
    writer.emit(COMPLETED, actor="run-manager",
        task_id=operation.get("task_id") or None,
        causation_id=request["metadata"].get("source_event_id") or None,
        correlation_id=request["request_id"], payload={
        "schema_version": "run-manager.reflect.v2", **request["metadata"], **result,
        "request_id": request["request_id"], "operation_id": operation["operation_id"],
        "result_ref": ref or {}, "apply_policy": "proposal_only",
    })


def reflection_pending(events, request_id: str) -> bool:
    return any(e.type == REQUESTED and e.payload.get("request_id") == request_id for e in events) and not any(
        e.type == COMPLETED and e.payload.get("request_id") == request_id for e in events
    )
