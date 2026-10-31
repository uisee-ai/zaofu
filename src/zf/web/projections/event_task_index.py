"""Batch the Board task-to-event substring matches without changing them."""
from __future__ import annotations

from dataclasses import dataclass
import threading

from zf.core.task.schema import Task

_MAX_GROUPED_EVENT_REFS = 250_000


@dataclass(frozen=True)
class _GroupedGeneration:
    source_key: tuple[str, int]
    task_ids: tuple[str, ...]
    reference_limit: int
    count: int
    last_row: tuple[int, object] | None
    value: dict[str, list[tuple[int, object]]] | None


# Keep only the latest project/generation. Values share decoded events and are
# bounded by the existing reference cap; no second complete ledger is retained.
_GROUPED_GENERATION: _GroupedGeneration | None = None
_GROUPED_LOCK = threading.Lock()


def cached_group_task_events(
    tasks: list[Task], events: list[tuple[int, object]], texts: list[str], *,
    source_key: tuple[str, int],
) -> dict[str, list[tuple[int, object]]] | None:
    """Reuse only a prefix certified by the event reader's decoded epoch.

    Rotation/replacement/decoder changes receive a new epoch. Appends preserve
    the old row objects. Never extend lists held by an in-flight reader.
    """
    global _GROUPED_GENERATION
    task_ids = tuple(task.id for task in tasks)
    with _GROUPED_LOCK:
        old = _GROUPED_GENERATION
    reusable = (
        old is not None and old.source_key == source_key
        and old.task_ids == task_ids and old.reference_limit == _MAX_GROUPED_EVENT_REFS
        and old.count <= len(events)
        and (not old.count or events[old.count - 1] is old.last_row)
    )
    if reusable and old is not None:
        if old.count == len(events):
            return old.value
        tail = group_task_events(tasks, events[old.count:], texts[old.count:])
        if old.value is None or tail is None:
            value = None
        elif sum(map(len, old.value.values())) + sum(map(len, tail.values())) > _MAX_GROUPED_EVENT_REFS:
            value = None
        else:
            value = {task_id: old.value[task_id] + tail[task_id] for task_id in old.value}
    else:
        value = group_task_events(tasks, events, texts)
    entry = _GroupedGeneration(source_key, task_ids, _MAX_GROUPED_EVENT_REFS,
                               len(events), events[-1] if events else None, value)
    with _GROUPED_LOCK:
        _GROUPED_GENERATION = entry
    return value


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
