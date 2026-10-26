"""Count canonical task completions across legacy and Task Pipeline ledgers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def count_completed_tasks(events: Iterable[Mapping[str, Any]]) -> int:
    """Count task identities once; preserve legacy rows without task IDs.

    Pipeline completion is a kernel integration fact, not a worker assertion.
    Task IDs are unique within the canonical TaskStore being evaluated.
    """
    completed: set[str] = set()
    anonymous = 0
    for event in events:
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        legacy = (
            event.get("type") == "task.status_changed"
            and payload.get("to") == "done"
        )
        integrated = (
            event.get("type") == "task.done"
            and event.get("origin") == "kernel"
            and payload.get("authority") == "task_integration_receipt"
        )
        if not (legacy or integrated):
            continue
        task_id = str(event.get("task_id") or "")
        if task_id:
            completed.add(task_id)
        elif legacy:
            anonymous += 1
    return len(completed) + anonymous
