"""Fence late recovery failures after their exact task replacements are admitted."""

from __future__ import annotations

from zf.core.events.model import ZfEvent


def reader_failure_was_replaced(events: list[ZfEvent], failure: ZfEvent) -> bool:
    """Require causal replacement plus WRC admission, not just a newer plan."""
    fanout_id = str(failure.payload.get("fanout_id") or "")
    if not fanout_id:
        return False
    starts = [event for event in events if event.type == "fanout.started"
              and event.payload.get("fanout_id") == fanout_id]
    if len(starts) != 1:
        return False
    by_id = {event.id: event for event in events}
    trigger = by_id.get(str(starts[0].payload.get("trigger_event_id") or ""))
    if trigger is None:
        return False
    run_id = _run_id(trigger)
    if not run_id or _run_id(failure) != run_id:
        return False
    source_id = str(trigger.payload.get("rework_of") or "")
    source = by_id.get(source_id)
    if source is None or _run_id(source) != run_id:
        return False
    affected: set[str] = set()
    for event in (trigger, failure):
        affected.update(_strings(event.payload.get("affected_task_ids")))
        task_id = str(event.payload.get("task_id") or event.task_id or "")
        if task_id:
            affected.add(task_id)
    if not affected:
        return False
    source_index = events.index(source)
    for admitted in events[source_index + 1:]:
        payload = admitted.payload
        if (admitted.type != "task.pipeline.generation.admitted"
                or admitted.origin != "kernel" or _run_id(admitted) != run_id
                or payload.get("schema_version") != "task-pipeline-generation.v1"):
            continue
        ready = by_id.get(str(payload.get("trigger_event_id") or ""))
        if (ready is None or ready.type != "task_map.ready" or ready.origin != "kernel"
                or _run_id(ready) != run_id or ready.payload.get("rework_of") != source_id):
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
        if affected <= replaced:
            return True
    return False


def _run_id(event: ZfEvent) -> str:
    return str(event.payload.get("workflow_run_id") or event.payload.get("run_id")
               or event.correlation_id or "")


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []
