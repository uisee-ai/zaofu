"""Generation-scoped helpers for candidate recovery projection."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


def reset_generation_caches(
    event: ZfEvent,
    payload: dict,
    *,
    boundary_event_types: set[str] | frozenset[str],
    feedback_by_trace: dict[str, list[str]],
    failed_task_ids_by_trace: dict[str, set[str]],
    gap_tasks_by_trace: dict[str, list[dict[str, Any]]],
) -> None:
    if event.type not in boundary_event_types:
        return
    trace_id = str(payload.get("trace_id") or event.correlation_id or "")
    if not trace_id:
        return
    feedback_by_trace.pop(trace_id, None)
    failed_task_ids_by_trace.pop(trace_id, None)
    gap_tasks_by_trace.pop(trace_id, None)


def task_ids_from_payload(payload: dict) -> set[str]:
    task_ids: set[str] = set()
    task_id = payload.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        task_ids.add(task_id.strip())
    # Completed slices are candidate inputs, not failed-task attribution.
    # Reopening them after a candidate-only failure breaks writer admission.
    for key in ("task_ids", "failed_task_ids"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str) and item.strip():
                task_ids.add(item.strip())
            elif isinstance(item, dict):
                value = str(item.get("task_id") or item.get("id") or "").strip()
                if value:
                    task_ids.add(value)
    findings = payload.get("findings")
    if not isinstance(findings, list):
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        findings = report.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            value = str(item.get("task_id") or item.get("task") or "").strip()
            if value:
                task_ids.add(value)
    return task_ids


__all__ = ["reset_generation_caches", "task_ids_from_payload"]
