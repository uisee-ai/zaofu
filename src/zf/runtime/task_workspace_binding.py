"""Read-only resolution of an admitted task workspace for recovery callers.

An invalid task binding is an error, never permission to use a role checkout.
This resolver does not grant a lease, stop a provider, or restore any files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_run_binding import canonical_task_run
from zf.runtime.workdirs import WorkdirManager


def resolve_task_workspace(
    *, state_dir: Path, project_root: Path, config: Any, task: Any,
    events: Sequence[ZfEvent], require_live: bool = False,
) -> Path | None:
    """Return None only for a task without Task Pipeline workspace history."""
    if task is None:
        return None
    dispatched = [event for event in events
                  if event.type == "task.pipeline.stage.dispatched"
                  and event.task_id == task.id]
    context = task_pipeline_generation_contexts(events).get(task.id)
    if not dispatched and not context:
        return None
    run_id, strict = canonical_task_run(task, events)
    dispatch_id = str(task.active_dispatch_id or "")
    if (strict or context) and not dispatch_id:
        raise ValueError("task workspace has no current dispatch identity")
    candidates = [event for event in dispatched
                  if (not dispatch_id or event.payload.get("dispatch_id") == dispatch_id)
                  and (not strict or (run_id and
                       (event.payload.get("workflow_run_id") or event.correlation_id) == run_id))]
    if not candidates:
        raise ValueError("task workspace has no current dispatch binding")
    event = candidates[-1]
    payload = event.payload
    owner = payload.get("role_instance") or payload.get("assignee")
    if owner != task.assigned_to:
        raise ValueError("task workspace owner differs from canonical assignee")
    if context and (any(payload.get(key) != context.get(key)
                        for key in ("workflow_run_id", "task_map_generation"))
                    or (event.causation_id and event.causation_id != context.get("generation_admitted_event_id"))):
        raise ValueError("task workspace generation is stale")
    attempt_id = str(payload.get("attempt_id") or "")
    if require_live and any(
        row.task_id == task.id and row.payload.get("attempt_id") == attempt_id
        and row.type in {"task.attempt.failed", "task.attempt.completed",
                         "task.attempt.succeeded", "task.attempt.superseded", "task.attempt.expired"}
        for row in events
    ):
        raise ValueError("task workspace attempt is terminal; recovery requires a current dispatch")
    raw = str(payload.get("workdir") or "")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    candidate = candidate.resolve()
    task_root = (WorkdirManager(state_dir=state_dir, project_root=project_root,
                               config=config).root / "tasks").resolve()
    if (not raw or candidate.name != "project" or not candidate.is_relative_to(task_root)
            or not candidate.is_dir() or not (candidate / ".git").exists()):
        raise ValueError("task workspace is missing or outside the configured task root")
    metadata = candidate.parent / "meta.json"
    try:
        body = json.loads(metadata.read_text()) if metadata.exists() else None
        if body is None and (dispatch_id or context):
            raise ValueError("task workspace metadata is missing")
        if body is not None:
            expected = {"task_id": task.id,
                        "workflow_run_id": payload.get("workflow_run_id") or event.correlation_id,
                        "task_map_generation": payload.get("task_map_generation"),
                        "workspace_generation": payload.get("workspace_generation")}
            if not isinstance(body, dict) or any(body.get(key) != value for key, value in expected.items()):
                raise ValueError("task workspace metadata does not match the current dispatch")
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("task workspace metadata is unreadable") from exc
    return candidate
