"""Validate every current Task receipt before a Run-wide candidate freeze."""

from pathlib import Path
from typing import Any, Mapping
from zf.core.events.model import ZfEvent


def collect_freeze_receipts(
    runtime: Any,
    *,
    events: list[ZfEvent],
    workflow_run_id: str,
    task_ids: list[str],
    task_contexts: Mapping[str, Mapping[str, Any]],
) -> (
    tuple[
        list[tuple[ZfEvent, dict[str, Any], dict[str, Any]]], list[str], list[ZfEvent]
    ]
    | None
):
    integrated: list[tuple[ZfEvent, dict[str, Any], dict[str, Any]]] = []
    completed_task_ids: list[str] = []
    superseded: list[ZfEvent] = []
    from zf.runtime.candidate_incremental import hydrate_task_integration_receipt
    from zf.runtime.candidates import CandidateRebuilder
    from zf.runtime.task_pipeline_terminal import (
        _authorized_supersession_event,
        _latest_integration_event,
        _generation_has_active_work,
        _generation_sessions_archived,
    )

    rebuilder = CandidateRebuilder(
        state_dir=Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
        event_log=runtime.event_log,
    )
    groups: dict[str, tuple[Mapping[str, Any], list[str]]] = {}
    for task_id in task_ids:
        context = task_contexts[task_id]
        task_map_generation = str(context.get("task_map_generation") or "")
        key = str(context.get("generation_id") or task_map_generation)
        groups.setdefault(key, (context, []))[1].append(task_id)
        task = runtime.task_store.get(task_id)
        if task is None:
            return None
        if str(task.status) == "cancelled":
            supersession = _authorized_supersession_event(
                events,
                task_id=task_id,
                context=context,
            )
            if supersession is None:
                return None
            superseded.append(supersession)
            continue
        if str(task.status) != "done":
            return None
        event = _latest_integration_event(
            events,
            task_id=task_id,
            context=context,
        )
        if event is None:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        descriptor = payload.get("receipt_ref")
        if not isinstance(descriptor, Mapping):
            return None
        try:
            receipt = hydrate_task_integration_receipt(
                rebuilder,
                descriptor,
                task_id=task_id,
                workflow_run_id=workflow_run_id,
                task_map_generation=task_map_generation,
            )
        except Exception:
            return None
        integrated.append((event, receipt, dict(descriptor)))
        completed_task_ids.append(task_id)
    if not integrated:
        return None
    for context, ids in groups.values():
        if _generation_has_active_work(
            runtime, context, ids
        ) or not _generation_sessions_archived(
            events,
            workflow_run_id=workflow_run_id,
            task_map_generation=str(context.get("task_map_generation") or ""),
            task_ids=ids,
        ):
            return None
    return integrated, completed_task_ids, superseded
