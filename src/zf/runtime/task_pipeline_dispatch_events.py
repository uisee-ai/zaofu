"""Deduplicated Task Pipeline dispatch wait/defer occurrence events."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent


def emit_task_pipeline_dispatch_failure(runtime: Any, exc: Exception) -> None:
    runtime.event_writer.append(ZfEvent(
        type="kernel.housekeeping.failed", actor="orchestrator", origin="kernel",
        payload={
            "step": "task_pipeline_dispatch",
            "exc_type": type(exc).__name__,
            "exc_repr": repr(exc)[:500],
        },
    ))


def emit_task_pipeline_waiting_once(
    runtime: Any,
    *,
    task_id: str,
    stage: str,
    operation_generation: int,
    context: Mapping[str, Any],
    reason: str,
    detail: str,
) -> None:
    payload = {
        "schema_version": "task-pipeline-stage-waiting.v1",
        "workflow_run_id": str(context.get("workflow_run_id") or ""),
        "task_id": task_id,
        "task_map_generation": str(context.get("task_map_generation") or ""),
        "task_pipeline_stage": stage,
        "operation_generation": operation_generation,
        "reason": reason,
        "detail": detail[:500],
    }
    _emit_stage_occurrence_once(
        runtime,
        event_type="task.pipeline.stage.waiting",
        task_id=task_id,
        payload=payload,
        causation_id=str(context.get("generation_admitted_event_id") or ""),
    )


def emit_task_pipeline_dispatch_deferred_once(
    runtime: Any,
    *,
    task_id: str,
    stage: str,
    operation_generation: int,
    context: Mapping[str, Any],
    reason: str,
) -> None:
    payload = {
        "schema_version": "task-pipeline-stage-dispatch-deferred.v1",
        "workflow_run_id": str(context.get("workflow_run_id") or ""),
        "task_id": task_id,
        "task_map_generation": str(context.get("task_map_generation") or ""),
        "task_pipeline_stage": stage,
        "operation_generation": operation_generation,
        "reason": reason,
    }
    _emit_stage_occurrence_once(
        runtime,
        event_type="task.pipeline.stage.dispatch_deferred",
        task_id=task_id,
        payload=payload,
        causation_id=str(context.get("generation_admitted_event_id") or ""),
    )


def _emit_stage_occurrence_once(
    runtime: Any,
    *,
    event_type: str,
    task_id: str,
    payload: Mapping[str, Any],
    causation_id: str,
) -> None:
    identity = (
        str(payload.get("workflow_run_id") or ""),
        task_id,
        str(payload.get("task_map_generation") or ""),
        str(payload.get("task_pipeline_stage") or ""),
        int(payload.get("operation_generation") or 0),
        str(payload.get("reason") or ""),
    )
    for event in reversed(runtime.event_log.read_all()):
        if event.type != event_type or event.task_id != task_id:
            continue
        existing = event.payload if isinstance(event.payload, dict) else {}
        existing_identity = (
            str(existing.get("workflow_run_id") or ""),
            task_id,
            str(existing.get("task_map_generation") or ""),
            str(existing.get("task_pipeline_stage") or ""),
            int(existing.get("operation_generation") or 0),
            str(existing.get("reason") or ""),
        )
        if existing_identity == identity:
            return
        break
    runtime.event_writer.append(ZfEvent(
        type=event_type,
        actor="orchestrator",
        origin="kernel",
        task_id=task_id,
        payload=dict(payload),
        causation_id=causation_id or None,
        correlation_id=str(payload.get("workflow_run_id") or "") or None,
    ))


__all__ = [
    "emit_task_pipeline_dispatch_deferred_once",
    "emit_task_pipeline_waiting_once",
]
