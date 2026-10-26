"""Publish and validate the task projection for a prepared pipeline delivery."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task


def current_pipeline_capsule_task(
    runtime: Any, *, task_id: str, snapshot: Mapping[str, Any],
) -> Task:
    from zf.runtime.task_doc import compute_task_capsule_revisions
    from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError

    task = runtime.task_store.get(task_id)
    if task is None or compute_task_capsule_revisions(task)[
        "contract_revision"
    ] != snapshot.get("contract_revision"):
        raise TaskPipelineRuntimeError("task capsule canonical contract does not match snapshot")
    return task


def publish_pipeline_capsule(
    runtime: Any,
    *,
    task_id: str,
    snapshot: Mapping[str, Any],
    dispatch_id: str,
    role_instance: str,
    generation_event_id: str,
) -> None:
    from zf.runtime.task_doc import (
        verify_task_capsule,
        write_task_doc,
    )
    from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError

    # Re-read canonical state: selection may predate a concurrent plan refresh.
    task = current_pipeline_capsule_task(runtime, task_id=task_id, snapshot=snapshot)
    state_dir = Path(runtime.state_dir)
    # Bind the worker-facing projection to this delivery, without claiming
    # successful delivery in TaskStore before the transport has accepted it.
    task = deepcopy(task)
    task.active_dispatch_id = dispatch_id
    task.assigned_to = role_instance
    doc = write_task_doc(
        state_dir,
        task,
        dispatch_id=dispatch_id,
        source_event=generation_event_id,
        project_root=Path(runtime.project_root),
    )
    current = runtime.task_store.get(task_id)
    errors = (
        verify_task_capsule(
            state_dir, current, expected_dispatch_id=dispatch_id,
            expected_role_instance=role_instance,
        )
        if current is not None
        else ["task_missing"]
    )
    if errors:
        raise TaskPipelineRuntimeError(
            "task capsule preflight failed: " + ", ".join(errors)
        )
    # This is a projection write only; never write the selected contract back.
    runtime.event_writer.append(
        ZfEvent(
            type="task.doc.published",
            actor="zf-cli",
            origin="kernel",
            task_id=task_id,
            causation_id=generation_event_id or None,
            payload={
                "dispatch_id": dispatch_id,
                "task_doc": str(doc.path),
                "manifest": str(doc.manifest_path),
                "source_doc": str(doc.source_path),
                "source_revision": doc.source_revision,
                "contract_revision": doc.contract_revision,
                "capsule_revision": doc.capsule_revision,
                "source": "task_pipeline",
            },
        )
    )


def verify_pipeline_delivery_capsule(
    runtime: Any, *, task_id: str, dispatch_id: str, role_instance: str,
) -> None:
    """Recheck the projection at the transport boundary after call preparation."""
    from zf.runtime.task_doc import verify_task_capsule
    from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError

    current = runtime.task_store.get(task_id)
    errors = (
        verify_task_capsule(
            Path(runtime.state_dir), current, expected_dispatch_id=dispatch_id,
            expected_role_instance=role_instance,
        )
        if current is not None else ["task_missing"]
    )
    if errors:
        raise TaskPipelineRuntimeError("task capsule delivery preflight failed: " + ", ".join(errors))
