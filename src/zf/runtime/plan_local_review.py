"""Mechanical comparison supporting a separately evidenced local Plan review.

The planner and independent critic own semantic scope. These checks cannot
establish semantic completeness and never approve, apply, or change a Plan.
"""
from __future__ import annotations

from typing import Any, Mapping

from zf.runtime.task_scoped_plan_inputs import task_input_paths


def scoped_port_paths(
    *, old_map: Mapping[str, Any], new_map: Mapping[str, Any],
    old_port: Mapping[str, Any], new_port: Mapping[str, Any],
    affected_task_ids: list[str],
) -> tuple[list[str], list[str]] | None:
    """Return exact old/new selectors, or require the full source on doubt."""
    scope = set(affected_task_ids)
    if not scope or len(scope) != len(affected_task_ids) or any(not item for item in scope):
        return None
    if {k: v for k, v in old_map.items() if k != "tasks"} != {
        k: v for k, v in new_map.items() if k != "tasks"
    }:
        return None
    old_tasks = _task_rows(old_map)
    new_tasks = _task_rows(new_map)
    if old_tasks is None or new_tasks is None or old_tasks.keys() != new_tasks.keys() or not scope <= old_tasks.keys():
        return None
    if any(old_tasks[key] != new_tasks[key] for key in old_tasks.keys() - scope):
        return None
    # Include prerequisites and downstream consumers in either graph.
    # Unknown or malformed dependencies make the proof unavailable.
    downstream = set(scope)
    edges: dict[str, set[str]] = {}
    for task_id in old_tasks:
        dependencies: set[str] = set()
        for row in (old_tasks[task_id], new_tasks[task_id]):
            raw = row.get("blocked_by", [])
            if not isinstance(raw, list) or any(not isinstance(item, str) or item not in old_tasks for item in raw):
                return None
            dependencies.update(raw)
        edges[task_id] = dependencies
    changed = True
    while changed:
        before = len(downstream)
        downstream.update(task_id for task_id, deps in edges.items() if deps & downstream)
        downstream.update(dep for task_id in tuple(downstream) for dep in edges[task_id])
        changed = len(downstream) != before
    if (old_port.get("schema_version") != "task-scoped-plan-input.v1"
            or new_port.get("schema_version") != "task-scoped-plan-input.v1"
            or old_port.get("shared") != new_port.get("shared")):
        return None
    try:
        old_paths = ["$.shared"]
        new_paths = ["$.shared"]
        for task_id in sorted(downstream):
            old_paths.extend(task_input_paths(old_port, task_id=task_id)[1:])
            new_paths.extend(task_input_paths(new_port, task_id=task_id)[1:])
        old_rows = {row["task_id"]: row for row in old_port["tasks"]}
        new_rows = {row["task_id"]: row for row in new_port["tasks"]}
    except (KeyError, TypeError, ValueError):
        return None
    if old_rows.keys() != new_rows.keys() or not old_rows.keys() <= old_tasks.keys():
        return None
    if any(old_rows[key] != new_rows[key] for key in old_rows.keys() - scope):
        return None
    return old_paths, new_paths


def _task_rows(task_map: Mapping[str, Any]) -> dict[str, Mapping[str, Any]] | None:
    raw = task_map.get("tasks")
    if not isinstance(raw, list):
        return None
    result: dict[str, Mapping[str, Any]] = {}
    for task in raw:
        if not isinstance(task, Mapping):
            return None
        task_id = task.get("task_id") or task.get("id")
        if not isinstance(task_id, str) or not task_id or task_id in result:
            return None
        result[task_id] = task
    return result
