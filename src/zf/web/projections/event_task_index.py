"""Batch the Board task-to-event substring matches without changing them."""
from __future__ import annotations

from zf.core.task.schema import Task

_MAX_GROUPED_EVENT_REFS = 250_000


def group_task_events(tasks: list[Task], events: list[tuple[int, object]], texts: list[str]) -> dict[str, list[tuple[int, object]]] | None:
    """Batch existing lowercase substring matches without changing their order."""
    grouped = {task.id: [] for task in tasks}
    lowered: dict[str, list[str]] = {}
    prefixes: dict[str, list[str]] = {}
    for task in tasks:
        needle = task.id.lower()
        if needle:
            lowered.setdefault(needle, []).append(task.id)
    for needle in lowered:
        prefixes.setdefault(needle[:5], []).append(needle)
    references = 0
    for row, text in zip(events, texts):
        event_id = getattr(row[1], "task_id", None)
        matched = {event_id} if isinstance(event_id, str) and event_id in grouped else set()
        for prefix, needles in prefixes.items():
            # A singleton can use the original full substring search directly.
            if len(needles) == 1:
                needle = needles[0]
                if needle in text:
                    matched.update(lowered[needle])
            elif prefix in text:
                for needle in needles:
                    if needle in text:
                        matched.update(lowered[needle])
        references += len(matched)
        if references > _MAX_GROUPED_EVENT_REFS:
            return None  # Use the original per-task scan when sharing is too large.
        for task_id in matched:
            grouped[task_id].append(row)
    return grouped
