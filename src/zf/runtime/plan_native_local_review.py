"""Exact declared native partitions; no inference of semantic scope."""
from typing import Any, Mapping

from zf.runtime.artifact_read_ledger import _select_json_path
from zf.runtime.task_scoped_plan_inputs import task_input_paths


def native_port_paths(old: Mapping[str, Any], new: Mapping[str, Any], *,
                      changed: set[str], reviewed: set[str],
                      task_ids_in_map: set[str]) -> tuple[list[str], list[str]] | None:
    try:
        policy = old["task_input_policy"]
        if policy != new["task_input_policy"] or old.get("schema_version") != new.get("schema_version"):
            return None
        task_ids = {row["task_id"] for row in policy["tasks"]}
        if not reviewed <= task_ids or not task_ids <= task_ids_in_map:
            return None
        # task_input_paths validates shape, selectors and complete leaf coverage.
        for task_id in task_ids:
            before = task_input_paths(old, task_id=task_id)
            after = task_input_paths(new, task_id=task_id)
            if before != after:
                return None
            if task_id not in changed and any(
                _select_json_path(old, path) != _select_json_path(new, path) for path in before
            ):
                return None
        shared = policy["shared_paths"]
        if any(_select_json_path(old, path) != _select_json_path(new, path) for path in shared):
            return None
        paths = list(dict.fromkeys(path for task_id in sorted(reviewed)
                                  for path in task_input_paths(new, task_id=task_id)))
        return paths, list(paths)
    except (KeyError, TypeError, ValueError):
        return None
