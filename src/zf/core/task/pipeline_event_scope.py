"""Select current pipeline-generation evidence for read-only task projections."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeVar

from zf.core.task.schema import Task


_Event = TypeVar("_Event")
_SCOPE_KEYS = ("workflow_run_id", "task_map_generation", "operation_generation")


def _payload(event: object) -> Mapping:
    body = getattr(event, "payload", None)
    return body if isinstance(body, Mapping) else {}


def task_pipeline_event_scope(task: Task, events: Iterable[_Event]) -> list[_Event]:
    """Keep one implementation generation, including its downstream verifier.

    The active dispatch pins the scope when present. Without pipeline dispatch
    evidence, legacy projections keep their existing event history unchanged.
    """
    rows = list(events)
    dispatches = [
        (index, event) for index, event in enumerate(rows)
        if getattr(event, "type", "") == "task.pipeline.stage.dispatched"
        and getattr(event, "origin", "") == "kernel"
        and getattr(event, "task_id", None) == task.id
        and _payload(event).get("task_pipeline_stage") in {"impl", "verify"}
        and all(str(_payload(event).get(key) or "") for key in _SCOPE_KEYS)
    ]
    anchors = [
        pair for pair in dispatches
        if not task.active_dispatch_id
        or _payload(pair[1]).get("dispatch_id") == task.active_dispatch_id
    ]
    if not anchors:
        return rows
    anchor_index, anchor = anchors[-1]
    scope = {key: str(_payload(anchor)[key]) for key in _SCOPE_KEYS}
    starts = [
        index for index, event in dispatches
        if index <= anchor_index
        and _payload(event).get("task_pipeline_stage") == "impl"
        and all(str(_payload(event)[key]) == value for key, value in scope.items())
    ]
    start = min(starts) if starts else anchor_index
    return [
        event for event in rows[start:]
        if getattr(event, "task_id", None) in {None, "", task.id}
        and all(key not in _payload(event) or str(_payload(event)[key]) == value
                for key, value in scope.items())
    ]
