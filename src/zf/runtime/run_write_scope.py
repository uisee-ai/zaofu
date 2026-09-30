"""Keep concurrent planner write permissions within their admitted request."""

from pathlib import Path
from typing import Any

from zf.core.events import EventLog
from zf.runtime.run_isolation import _normalize_scope_path, _scope_paths, _scope_values


def validate_run_write_scopes(
    state_dir: Path, *, run_id: str, task_items: list[dict[str, Any]],
) -> None:
    """Reject widening before writer Tasks or operations are materialized."""
    events = EventLog(state_dir / "events.jsonl").read_all()
    admissions = [event for event in events if event.type == "run.admission.admitted"
                  and event.payload.get("workflow_run_id") == run_id
                  and event.payload.get("policy_mode") == "concurrent"]
    if not admissions:
        return
    source_id = admissions[0].payload.get("source_event_id")
    source = next((event for event in events if event.id == source_id
                   and event.type == "workflow.invoke.requested"
                   and event.payload.get("workflow_run_id") == run_id), None)
    if source is None:
        raise ValueError("concurrent writer has no admitted request scope binding")
    raw = _scope_values(source.payload)
    if not raw:
        return
    scopes = _scope_paths(source.payload)
    if any(not _normalize_scope_path(value) for value in raw):
        raise ValueError("concurrent writer has invalid admitted request scope")
    for task in task_items:
        paths = task.get("allowed_paths") or []
        if not paths:
            raise ValueError(f"writer {task.get('task_id')} is outside admitted request scope: missing paths")
        for raw_path in paths:
            path = str(raw_path).replace("\\", "/")
            if path.endswith("/**"):
                path = path[:-3]
            path = _normalize_scope_path(path)
            if not path or not any(path == scope or path.startswith(scope + "/") for scope in scopes):
                raise ValueError(
                    f"writer {task.get('task_id')} is outside admitted request scope: {raw_path}"
                )
