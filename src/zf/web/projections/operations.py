"""Indexed Web projections for task and workflow operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.operation_projection import (
    project_operation,
    project_task_operations,
    project_workflow_operation,
)
from zf.web.projections import read_model


def task_operations(
    state_dir: Path,
    task_id: str,
    *,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    return _project(
        state_dir,
        ref_kind="task",
        ref_id=task_id,
        config=config,
        projector=lambda events: project_task_operations(
            state_dir,
            task_id,
            events=events,
        ),
        fallback=lambda: project_task_operations(state_dir, task_id),
    )


def dispatch_operation(
    state_dir: Path,
    dispatch_id: str,
    *,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    return _project(
        state_dir,
        ref_kind="dispatch_id",
        ref_id=dispatch_id,
        config=config,
        projector=lambda events: project_operation(
            state_dir,
            dispatch_id,
            events=events,
        ),
        fallback=lambda: project_operation(state_dir, dispatch_id),
    )


def workflow_operation(
    state_dir: Path,
    operation_id: str,
    *,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    return _project(
        state_dir,
        ref_kind="operation_id",
        ref_id=operation_id,
        config=config,
        projector=lambda events: project_workflow_operation(
            state_dir,
            operation_id,
            events=events,
        ),
        fallback=lambda: project_workflow_operation(state_dir, operation_id),
    )


def _project(
    state_dir: Path,
    *,
    ref_kind: str,
    ref_id: str,
    config: ZfConfig | None,
    projector: Callable[[list[ZfEvent]], dict[str, Any]],
    fallback: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    events = read_model.hydrate_events_by_ref(
        state_dir,
        ref_kind=ref_kind,
        ref_id=ref_id,
        config=config,
    )
    if events is None:
        projection = fallback()
        projection["source"] = "events.jsonl"
        projection["projection_state"] = "fallback"
        return projection
    projection = projector(events)
    status = read_model.projection_status(state_dir)
    projection["source"] = "read_model.sqlite"
    projection["projection_state"] = status.get("projection_state", "unknown")
    projection["projection_lag"] = status.get("projection_lag")
    return projection


__all__ = [
    "dispatch_operation",
    "task_operations",
    "workflow_operation",
]
