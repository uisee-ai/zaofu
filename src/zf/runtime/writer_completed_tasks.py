"""Completed writer inputs retained across gap-only batch recovery."""

from __future__ import annotations

from typing import Any, Mapping


def completed_writer_task_ids(manifest: Mapping[str, Any]) -> list[str]:
    children = [child for child in manifest.get("children", []) or []
                if isinstance(child, dict)]
    current = {str(child.get("task_id") or "") for child in children}
    trigger = manifest.get("trigger_payload")
    trigger = trigger if isinstance(trigger, dict) else {}
    inherited = trigger.get("completed_task_ids", [])
    inherited = inherited if isinstance(inherited, list) else []
    # A task explicitly present in this batch must succeed again before it
    # contributes; an old completion must not mask its pending/failed retry.
    retained = [task_id for task_id in inherited
                if isinstance(task_id, str) and task_id and task_id not in current]
    if trigger.get("resume_scope") != "gap_tasks_only":
        retained = []
    return list(dict.fromkeys([
        *retained,
        *(str(child["task_id"]) for child in children
          if child.get("status") == "completed" and child.get("task_id")),
    ]))
