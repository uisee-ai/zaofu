"""Batch the Board task-to-event substring matches without changing them."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import threading

from zf.core.task.schema import Task

_MAX_GROUPED_EVENT_REFS = 250_000


@dataclass(frozen=True)
class _GroupedGeneration:
    source_key: tuple[str, int]
    source_fingerprint: tuple | None
    task_ids: tuple[str, ...]
    reference_limit: int
    count: int
    last_row: tuple[int, object] | None
    input_digests: tuple[bytes, ...]
    value: dict[str, list[int]] | None


# Retain only sequence numbers for four projects, each within the reference
# cap. Decoder eviction must not retain another project's large payloads here.
_GROUPED_GENERATIONS: OrderedDict[str, _GroupedGeneration] = OrderedDict()
_GROUPED_LOCK = threading.Lock()


def _matching_digests(events: list[tuple[int, object]], texts: list[str]) -> tuple[bytes, ...]:
    digests = []
    for (seq, event), text in zip(events, texts):
        task_id = getattr(event, "task_id", None)
        identity = json.dumps([seq, task_id if isinstance(task_id, str) else None])
        digest = hashlib.sha256(identity.encode())
        digest.update(b"\0")
        digest.update(text.encode("utf-8", "surrogatepass"))
        digests.append(digest.digest())
    return tuple(digests)


def cached_group_task_events(
    tasks: list[Task], events: list[tuple[int, object]], texts: list[str], *,
    source_key: tuple[str, int],
    source_fingerprint: tuple | None = None,
) -> dict[str, list[tuple[int, object]]] | None:
    """Reuse only a prefix certified by the event reader's decoded epoch.

    Rotation/replacement/decoder changes receive a new epoch. Appends preserve
    the old row objects. Never extend lists held by an in-flight reader.
    """
    if len(events) != len(texts) or len(events) > _MAX_GROUPED_EVENT_REFS:
        return group_task_events(tasks, events, texts)
    task_ids = tuple(task.id for task in tasks)
    with _GROUPED_LOCK:
        old = _GROUPED_GENERATIONS.get(source_key[0])
    same_tasks = (old is not None and old.task_ids == task_ids
                  and old.reference_limit == _MAX_GROUPED_EVENT_REFS)
    same_source = (
        same_tasks and old is not None and source_fingerprint is not None
        and old.source_fingerprint == source_fingerprint and old.count == len(events)
    )
    same_prefix = (
        same_tasks and old is not None and old.source_key == source_key
        and old.count <= len(events)
        and (not old.count or events[old.count - 1] is old.last_row)
    )
    if old is not None and same_source:
        input_digests = old.input_digests
    elif old is not None and same_prefix:
        input_digests = old.input_digests + _matching_digests(events[old.count:], texts[old.count:])
    else:
        input_digests = _matching_digests(events, texts)
        # Eviction can force a new decoded epoch while the ledger only appends.
        # Prove every matching input in the prefix, including task_id and seq;
        # event IDs or a growing file alone cannot certify this reuse.
        same_prefix = (same_tasks and old is not None and old.count <= len(events)
                       and input_digests[:old.count] == old.input_digests)
    if old is not None and (same_source or same_prefix and old.count == len(events)):
        value = old.value
    elif same_prefix and old is not None:
        tail = group_task_events(tasks, events[old.count:], texts[old.count:])
        if old.value is None or tail is None:
            value = None
        elif sum(map(len, old.value.values())) + sum(map(len, tail.values())) > _MAX_GROUPED_EVENT_REFS:
            value = None
        else:
            value = {task_id: old.value[task_id] + [seq for seq, _ in tail[task_id]]
                     for task_id in old.value}
    else:
        grouped = group_task_events(tasks, events, texts)
        value = None if grouped is None else {
            task_id: [seq for seq, _ in matches] for task_id, matches in grouped.items()
        }
    entry = _GroupedGeneration(source_key, source_fingerprint, task_ids, _MAX_GROUPED_EVENT_REFS,
                               len(events), events[-1] if events else None, input_digests, value)
    with _GROUPED_LOCK:
        _GROUPED_GENERATIONS[source_key[0]] = entry
        _GROUPED_GENERATIONS.move_to_end(source_key[0])
        while len(_GROUPED_GENERATIONS) > 4:
            _GROUPED_GENERATIONS.popitem(last=False)
    if value is None:
        return None
    by_seq = {row[0]: row for row in events}
    return {task_id: [by_seq[seq] for seq in matches] for task_id, matches in value.items()}


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
