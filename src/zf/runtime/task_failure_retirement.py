"""Retire task diagnostics from later canonical terminal facts, never activity."""

from zf.core.events.model import ZfEvent


def task_failure_retired(events: list[ZfEvent], source: ZfEvent) -> bool | None:
    """None leaves non-task failures with their existing progress policy."""
    if source.type not in {"task.attempt.failed", "task.pipeline.verify.failed"}:
        return None
    identity = _identity(source)
    if identity is None:
        return False
    seen_source = False
    for event in events:
        if event.id == source.id:
            seen_source = True
            continue
        if not seen_source or event.origin != "kernel" or _identity(event) != identity:
            continue
        if event.type == "task.done":
            return True
        if (event.type == "task.superseded"
                and event.payload.get("status") == "cancelled"
                and event.payload.get("superseded_by_task_map_ref")):
            return True
    return False


def _identity(event: ZfEvent) -> tuple[str, str] | None:
    payload = event.payload
    task_id = str(event.task_id or payload.get("task_id") or "")
    if event.task_id and payload.get("task_id") not in (None, "", event.task_id):
        return None
    run_id = str(payload.get("workflow_run_id") or payload.get("run_id")
                 or event.correlation_id or "")
    return (run_id, task_id) if run_id and task_id else None
