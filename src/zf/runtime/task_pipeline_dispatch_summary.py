"""Small, read-only scheduling view over the existing Task Pipeline projection."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping


def build_dispatch_summary(
    projection: Mapping[str, Any], *, workflow_run_id: str = "",
    task_ids: set[str] | None = None,
) -> dict[str, Any]:
    tasks = [
        row for row in projection.get("tasks", [])
        if (not workflow_run_id or row.get("workflow_run_id") == workflow_run_id)
        and (task_ids is None or row.get("task_id") in task_ids)
    ]
    selected = {row["task_id"] for row in tasks}
    # Projection operations are already ordered by task, generation and stage.
    latest = {
        row["task_id"]: row for row in projection.get("operations", [])
        if row.get("task_id") in selected
    }
    rows = []
    for task in tasks:
        operation = latest.get(task["task_id"], {})
        stage = str(task.get("pipeline_stage") or "unknown")
        if stage in {"done", "cancelled"}:
            execution = stage
        elif task.get("active_operation_count", 0):
            execution = "active"
        elif stage in {"blocked", "admission_blocked", "replan_requested"}:
            execution = "blocked"
        elif stage.endswith("_ready"):
            execution = "ready"
        else:
            execution = "waiting"
        rows.append({
            **{key: task.get(key, "") for key in (
                "task_id", "title", "task_status", "pipeline_stage", "run_status",
                "workflow_run_id", "task_map_generation", "current_operation_id",
                "current_worker", "blocked_by", "blockers",
            )},
            "execution_status": execution,
            "latest_operation": {key: operation.get(key, "") for key in (
                "operation_id", "stage", "status", "semantic_verdict",
                "active_attempt_id", "operation_generation", "last_event_id",
                "reason", "control_result_ref",
            )},
        })
    return {
        "schema_version": "task-pipeline-dispatch-summary.v1",
        "is_derived_projection": True,
        "workflow_run_id": workflow_run_id,
        "source_projection_digest": projection.get("projection_digest", ""),
        "task_count": len(rows),
        "task_statuses": dict(Counter(row["task_status"] for row in rows)),
        "execution_statuses": dict(Counter(row["execution_status"] for row in rows)),
        "tasks": rows,
        # Capacity is shared by the project, not a per-run reservation.
        "capacity_scope": "project",
        "capacity": projection.get("capacity", {}),
        "backpressure": projection.get("backpressure", {}),
        "dispatchable": {
            stage: [row for row in values if row.get("task_id") in selected]
            for stage, values in projection.get("dispatchable", {}).items()
        },
    }
