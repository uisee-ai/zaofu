"""Collect digest-bound receipts from the current Run task frontier."""
from pathlib import Path
from typing import Any, Mapping
from zf.core.events.model import ZfEvent

def collect_freeze_receipts(runtime: Any, *, events: list[ZfEvent], workflow_run_id: str,
                            task_ids: list[str], task_contexts: Mapping[str, Mapping[str, Any]]):
    from zf.runtime.task_pipeline_terminal import _authorized_supersession_event, _latest_integration_event
    integrated: list[tuple[ZfEvent, dict[str, Any], dict[str, Any]]] = []
    completed_task_ids: list[str] = []
    superseded: list[ZfEvent] = []
    from zf.runtime.candidate_incremental import hydrate_task_integration_receipt
    from zf.runtime.candidates import CandidateRebuilder

    rebuilder = CandidateRebuilder(
        state_dir=Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
        event_log=runtime.event_log,
    )
    for task_id in task_ids:
        task_context = task_contexts[task_id]
        task_generation = str(task_context.get("task_map_generation") or "")
        task = runtime.task_store.get(task_id)
        if task is None:
            return None
        if str(task.status) == "cancelled":
            supersession = _authorized_supersession_event(
                events,
                task_id=task_id,
                context=task_context,
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
            context=task_context,
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
                task_map_generation=task_generation,
            )
        except Exception:
            return None
        integrated.append((event, receipt, dict(descriptor)))
        completed_task_ids.append(task_id)
    if not integrated:
        return None
    return integrated, completed_task_ids, superseded
