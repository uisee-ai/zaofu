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
    admitted_maps: dict[str, set[str]] = {}
    for event in events:
        if event.origin == "kernel" and event.type == "task.pipeline.generation.admitted":
            ref = str(event.payload.get("task_map_ref") or "")
            explicit_run = event.payload.get("workflow_run_id") or event.payload.get("run_id")
            if ref:
                admitted_maps.setdefault(ref, set()).add(_run_id(event) if explicit_run else "")
    for event in events:
        if event.id == source.id:
            seen_source = True
            continue
        if not seen_source or event.origin != "kernel":
            continue
        if event.type == "task.done" and _identity(event) == identity:
            return True
        if (event.type == "task.superseded"
                and event.payload.get("status") == "cancelled"
                and event.payload.get("superseded_by_task_map_ref")):
            terminal_identity = _identity(event)
            if terminal_identity is None and not any((
                event.payload.get("workflow_run_id"), event.payload.get("run_id"),
                event.correlation_id,
            )) and event.payload.get("source") == "writer_task_map_adoption":
                # Historical writer events omitted Run identity. Only an
                # admitted exact replacement map may resolve that missing value.
                # Writer cancellation can precede generation admission.
                runs = admitted_maps.get(str(event.payload["superseded_by_task_map_ref"]), set())
                task_id = _task_id(event)
                if len(runs) == 1 and task_id:
                    run_id = next(iter(runs))
                    terminal_identity = (run_id, task_id) if run_id else None
            if terminal_identity == identity:
                return True
    return False


def _task_id(event: ZfEvent) -> str:
    if event.task_id and event.payload.get("task_id") not in (None, "", event.task_id):
        return ""
    return str(event.task_id or event.payload.get("task_id") or "")


def _run_id(event: ZfEvent) -> str:
    runs = {str(event.payload.get(key) or "") for key in ("workflow_run_id", "run_id")}
    runs.discard("")
    if len(runs) > 1:
        return ""
    return next(iter(runs)) if runs else str(event.correlation_id or "")


def _identity(event: ZfEvent) -> tuple[str, str] | None:
    run_id, task_id = _run_id(event), _task_id(event)
    return (run_id, task_id) if run_id and task_id else None
