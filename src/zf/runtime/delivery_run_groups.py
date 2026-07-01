"""Run-group projection for Delivery cockpit."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.delivery_projection_common import (
    EventSlice,
    base_stage_id,
    dedupe,
    event_status,
    normalize_status,
    payload,
)


def build_run_groups(
    *,
    events: EventSlice,
    tasks: dict[str, Task],
    workflow_trace: dict[str, Any],
    task_flow: dict[str, Any],
) -> list[dict[str, Any]]:
    event_by_id = {event.id: (seq, event) for seq, event in events if event.id}
    groups = [
        _stage_run_group(run, event_by_id)
        for run in workflow_trace.get("stage_runs") or []
        if isinstance(run, dict)
    ]
    seen = {group["group_id"] for group in groups}
    groups.extend(
        _fanout_run_group(fanout, event_by_id)
        for fanout in workflow_trace.get("fanout_runs") or []
        if isinstance(fanout, dict)
        and str(fanout.get("fanout_id") or "")
        and str(fanout.get("fanout_id") or "") not in seen
    )
    return groups or _task_only_run_groups(tasks, task_flow)


def _stage_run_group(
    run: dict[str, Any],
    event_by_id: dict[str, tuple[int, ZfEvent]],
) -> dict[str, Any]:
    source_ids = [str(item) for item in run.get("source_event_ids") or [] if item]
    group_id = str(run.get("fanout_id") or f"stage:{run.get('stage_id') or run.get('node_id')}")
    return {
        "schema_version": "delivery-run-group.v1",
        "group_id": group_id,
        "stage_id": base_stage_id(str(run.get("stage_id") or "")),
        "node_id": str(run.get("node_id") or ""),
        "label": str(run.get("label") or run.get("stage_id") or group_id),
        "kind": str(run.get("kind") or "stage"),
        "operator_kind": str(run.get("operator_kind") or ""),
        "status": normalize_status(str(run.get("status") or "pending")),
        "started_at": str(run.get("started_at") or ""),
        "ended_at": str(run.get("ended_at") or ""),
        "duration_ms": run.get("duration_ms"),
        "task_ids": [str(item) for item in run.get("task_ids") or [] if item],
        "children": list(run.get("fanout_child_runs") or []),
        "steps": _steps_for_event_ids(source_ids, event_by_id),
        "metrics": dict(run.get("metrics") or {}),
        "verdict": dict(run.get("verdict") or {}),
        "artifact_refs": list(run.get("artifact_refs") or []),
        "source_event_ids": source_ids,
    }


def _fanout_run_group(
    fanout: dict[str, Any],
    event_by_id: dict[str, tuple[int, ZfEvent]],
) -> dict[str, Any]:
    source_ids = [str(item) for item in fanout.get("source_event_ids") or [] if item]
    fanout_id = str(fanout.get("fanout_id") or "")
    return {
        "schema_version": "delivery-run-group.v1",
        "group_id": fanout_id,
        "stage_id": base_stage_id(str(fanout.get("stage_id") or "")),
        "node_id": "",
        "label": str(fanout.get("stage_id") or fanout_id),
        "kind": "fanout",
        "operator_kind": str(fanout.get("topology") or ""),
        "status": normalize_status(str(fanout.get("status") or "running")),
        "started_at": str(fanout.get("started_at") or ""),
        "ended_at": str(fanout.get("ended_at") or ""),
        "duration_ms": fanout.get("duration_ms"),
        "task_ids": _fanout_task_ids(fanout),
        "children": list(fanout.get("child_runs") or []),
        "steps": _steps_for_event_ids(source_ids, event_by_id),
        "metrics": dict(fanout.get("metrics") or {}),
        "verdict": {},
        "artifact_refs": list(fanout.get("artifact_refs") or []),
        "source_event_ids": source_ids,
    }


def _steps_for_event_ids(
    source_ids: list[str],
    event_by_id: dict[str, tuple[int, ZfEvent]],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for event_id in source_ids[-40:]:
        item = event_by_id.get(event_id)
        if item is None:
            steps.append({"step_id": event_id, "event_id": event_id, "status": "unknown"})
            continue
        seq, event = item
        steps.append({
            "step_id": f"event:{seq}",
            "event_id": event.id,
            "event_type": event.type,
            "task_id": str(event.task_id or payload(event).get("task_id") or ""),
            "status": event_status(event),
            "ts": event.ts,
        })
    return steps


def _task_only_run_groups(
    tasks: dict[str, Task],
    task_flow: dict[str, Any],
) -> list[dict[str, Any]]:
    del tasks
    groups: list[dict[str, Any]] = []
    for stage in task_flow.get("stages") or []:
        task_ids = [str(item) for item in stage.get("task_ids") or []]
        if not task_ids:
            continue
        groups.append({
            "schema_version": "delivery-run-group.v1",
            "group_id": f"task-flow:{stage['stage_id']}",
            "stage_id": stage["stage_id"],
            "node_id": "",
            "label": stage["label"],
            "kind": "task_flow",
            "operator_kind": "",
            "status": stage["status"],
            "started_at": "",
            "ended_at": "",
            "duration_ms": None,
            "task_ids": task_ids,
            "children": [],
            "steps": [],
            "metrics": {"tasks_total": len(task_ids)},
            "verdict": {},
            "artifact_refs": [],
            "source_event_ids": [],
        })
    return groups


def _fanout_task_ids(fanout: dict[str, Any]) -> list[str]:
    return dedupe(
        str(child.get("task_id") or "")
        for child in fanout.get("child_runs") or []
        if isinstance(child, dict) and str(child.get("task_id") or "")
    )
