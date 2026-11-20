"""Preserve the current pipeline attempt's protocol across provider restarts."""

from pathlib import Path

from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task


def current_pipeline_recovery_briefing(
    state_dir: Path, role: str, task: Task,
) -> str | None:
    """Reuse the admitted dispatch instructions; never infer a terminal command."""
    store_path = state_dir / "task_attempts.json"
    run_id = task.execution_binding.workflow_run_id
    if not run_id or not store_path.exists():
        return None
    rows = [row for row in TaskAttemptStore(store_path).current_rows()
            if row.get("run_id") == run_id and row.get("task_id") == task.id
            and row.get("identity_version") == "operation-v2"]
    if not rows:
        return None
    active = [row for row in rows if row.get("status") in {"prepared", "delivering", "sent"}]
    if len(active) != 1:
        raise ValueError("pipeline recovery requires one active canonical attempt")
    row = active[0]
    if (row.get("dispatch_id") != task.active_dispatch_id
            or row.get("instance_id") != role or task.assigned_to != role):
        raise ValueError("pipeline recovery attempt does not match task/role binding")
    ref = str(row.get("briefing_ref") or "")
    path = Path(ref)
    if not path.is_absolute():
        path = state_dir / path
    if not ref or not path.resolve().is_relative_to((state_dir / "briefings").resolve()):
        raise ValueError("pipeline recovery briefing is outside the managed briefing directory")
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("pipeline recovery briefing is unavailable") from exc
    if (body.splitlines()[0:1] != [f"Active task: {task.id}"]
            or any(not row.get(key) or str(row[key]) not in body
                   for key in ("operation_id", "dispatch_id", "attempt_id", "lease_id"))):
        raise ValueError("pipeline recovery briefing does not match canonical attempt identity")
    return body + (
        "\n\n## Provider Recovery\n\n"
        "Continue only unfinished work in the existing Task Workspace. "
        "Inspect its Git state and task progress before repeating side effects. "
        "The Read Order, Controlled Artifact Inputs, and Output Contract above "
        "remain authoritative for this attempt. Do not use completion commands "
        "from a previous role or dispatch.\n"
    )
