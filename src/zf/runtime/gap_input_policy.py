"""Rebind explicit task input partitions when a gap amendment changes positions."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

_TASK_PATH = re.compile(r"^\$\.tasks\[(\d+)\](.*)$")


def rebind_gap_input_policy(
    base: dict[str, Any], amended: dict[str, Any], *,
    gap_tasks: list[dict[str, Any]], replacements: dict[str, list[str]],
) -> None:
    """Preserve declared read scope by identity, never infer a broad read grant."""
    original = base.get("task_input_policy")
    if original is None:
        return
    if not isinstance(original, dict):
        raise ValueError("task_input_policy must be an object")
    policy = deepcopy(original)
    old_ids = [str(row.get("task_id") or row.get("id") or "") for row in base["tasks"]]
    positions = {str(row.get("task_id") or row.get("id") or ""): i
                 for i, row in enumerate(amended["tasks"])}
    partitions = {row["task_id"]: row for row in policy.get("tasks", [])}

    def rebound(paths: list[str]) -> list[str]:
        out: list[str] = []
        for path in paths:
            match = _TASK_PATH.fullmatch(path)
            if match is None:
                candidates = [path]
            else:
                index = int(match[1])
                if index >= len(old_ids):
                    raise ValueError(f"task input path has no source task: {path}")
                old_id = old_ids[index]
                targets = [old_id] if old_id in positions else replacements.get(old_id, [])
                if not targets or any(target not in positions for target in targets):
                    raise ValueError(f"task input path has no successor: {path}")
                candidates = [f"$.tasks[{positions[target]}]{match[2]}" for target in targets]
            for candidate in candidates:
                if candidate not in out:
                    out.append(candidate)
        return out

    gaps = {row["task_id"]: row for row in gap_tasks}
    rows = []
    for task_id, index in positions.items():
        if task_id in partitions:
            row = deepcopy(partitions[task_id])
            row["paths"] = rebound(row["paths"])
        else:
            gap = gaps.get(task_id, {})
            parent_ids = list(dict.fromkeys(
                ([gap["parent_task_id"]] if gap.get("parent_task_id") else [])
                + list(gap.get("supersedes_task_ids") or [])
            ))
            if not parent_ids or any(parent not in partitions for parent in parent_ids):
                raise ValueError(f"gap task {task_id} has no explicit source input partition")
            paths = rebound([path for parent in parent_ids for path in partitions[parent]["paths"]])
            own = f"$.tasks[{index}]"
            if own not in paths:
                paths.append(own)
            row = {"task_id": task_id, "paths": paths}
        rows.append(row)
    shared = rebound(policy.get("shared_paths", []))
    for path in ("$.source_refs", "$.amend"):
        if path not in shared:
            shared.append(path)
    policy["shared_paths"] = shared
    policy["tasks"] = rows
    amended["task_input_policy"] = policy
