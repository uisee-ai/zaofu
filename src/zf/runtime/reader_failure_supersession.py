"""Share causal task replacement proof across recovery routing and readers."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.reader_replan_index import ReaderReplanIndex


def reader_manifest_replacement_admission(events: list[ZfEvent], manifest: dict) -> ZfEvent | None:
    trigger = manifest.get("trigger_payload") or {}
    return reader_replacement_admission(
        events, fanout_id=str(manifest.get("fanout_id") or ""),
        workflow_run_id=str(trigger.get("workflow_run_id") or manifest.get("trace_id") or ""),
        affected_task_ids={
            str(child.get("task_id") or (child.get("payload") or {}).get("task_id") or "")
            for child in manifest.get("children", []) if isinstance(child, dict)
        } - {""},
    )


def reader_failure_was_replaced(
    events: list[ZfEvent], failure: ZfEvent, *, event_index: ReaderReplanIndex | None = None,
) -> bool:
    affected = set(_strings(failure.payload.get("affected_task_ids")))
    task_id = str(failure.payload.get("task_id") or failure.task_id or "")
    if task_id:
        affected.add(task_id)
    return reader_replacement_admission(
        events, fanout_id=str(failure.payload.get("fanout_id") or ""),
        workflow_run_id=_run_id(failure), affected_task_ids=affected, event_index=event_index,
    ) is not None


def reader_replacement_admission(
    events: list[ZfEvent], *, fanout_id: str, workflow_run_id: str,
    affected_task_ids: set[str] | None = None,
    event_index: ReaderReplanIndex | None = None,
) -> ZfEvent | None:
    """Require causal replacement plus WRC admission, not just a newer plan."""
    if event_index is not None:
        event_index.require_snapshot(events)
    if not fanout_id:
        return None
    candidates = ([row for _, row in event_index.rows("fanout.started")]
                  if event_index is not None else events)
    starts = [event for event in candidates if event.type == "fanout.started"
              and event.payload.get("fanout_id") == fanout_id]
    if len(starts) != 1:
        return None
    by_id = event_index.by_id if event_index is not None else {event.id: event for event in events}
    trigger = by_id.get(str(starts[0].payload.get("trigger_event_id") or ""))
    if trigger is None:
        return None
    run_id = _run_id(trigger)
    if not run_id or workflow_run_id != run_id:
        return None
    source_id = str(trigger.payload.get("rework_of") or "")
    source = by_id.get(source_id)
    if source is None or _run_id(source) != run_id:
        return None
    from zf.runtime.plan_admission_reader_closeout import recovered_plan_admission

    recovered = recovered_plan_admission(
        events, source=source, trigger=trigger, workflow_run_id=run_id)
    if recovered is not None:
        return recovered
    affected = set(affected_task_ids or ())
    affected.update(_strings(trigger.payload.get("affected_task_ids")))
    task_id = str(trigger.payload.get("task_id") or trigger.task_id or "")
    if task_id:
        affected.add(task_id)
    return admitted_task_replacement(
        events, source_event_id=source_id, workflow_run_id=run_id,
        affected_task_ids=affected, event_index=event_index,
    )


def failure_has_admitted_replacement(
    events: list[ZfEvent], failed_event: ZfEvent | None, task_id: str,
) -> bool:
    if failed_event is None or not task_id:
        return False
    return admitted_task_replacement(
        events, source_event_id=failed_event.id,
        workflow_run_id=_run_id(failed_event), affected_task_ids={task_id},
    ) is not None


def admitted_task_replacement(
    events: list[ZfEvent], *, source_event_id: str, workflow_run_id: str,
    affected_task_ids: set[str],
    event_index: ReaderReplanIndex | None = None,
) -> ZfEvent | None:
    """Prove all affected tasks were replaced for this exact failed occurrence.

    Run Manager uses the same proof before requesting a scan that reader
    closeout uses afterward. A proposed map alone never retires recovery.
    """
    if event_index is not None:
        event_index.require_snapshot(events)
    if not workflow_run_id or not affected_task_ids:
        return None
    by_id = event_index.by_id if event_index is not None else {event.id: event for event in events}
    source = by_id.get(source_event_id)
    if source is None or _run_id(source) != workflow_run_id:
        return None
    source_index = event_index.positions[source.id] if event_index is not None else events.index(source)
    candidates = ([row for position, row in event_index.rows("task.pipeline.generation.admitted")
                   if position > source_index] if event_index is not None else events[source_index + 1:])
    for admitted in candidates:
        payload = admitted.payload
        if (admitted.type != "task.pipeline.generation.admitted"
                or admitted.origin != "kernel" or _run_id(admitted) != workflow_run_id
                or payload.get("schema_version") != "task-pipeline-generation.v1"):
            continue
        ready = by_id.get(str(payload.get("trigger_event_id") or ""))
        if (ready is None or ready.type != "task_map.ready" or ready.origin != "kernel"
                or _run_id(ready) != workflow_run_id
                or ready.payload.get("rework_of") != source_event_id):
            continue
        summary = ready.payload.get("rework_summary")
        gaps = summary.get("gap_tasks") if isinstance(summary, dict) else None
        if not isinstance(gaps, list):
            continue
        admitted_ids = set(_strings(payload.get("task_ids")))
        replacements: dict[str, set[str]] = {}
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            successor = str(gap.get("task_id") or "")
            for parent in _strings(gap.get("supersedes_task_ids")):
                replacements.setdefault(parent, set()).add(successor)
        replaced = {
            parent for parent, successors in replacements.items()
            if successors and "" not in successors and parent not in admitted_ids
            and successors <= admitted_ids
        }
        if affected_task_ids <= replaced:
            return admitted
    return None


def _run_id(event: ZfEvent) -> str:
    return str(event.payload.get("workflow_run_id") or event.payload.get("run_id")
               or event.correlation_id or "")


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []
