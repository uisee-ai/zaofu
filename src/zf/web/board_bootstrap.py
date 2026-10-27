"""TaskStore-only first paint; never impersonate a complete dashboard snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from zf.core.security.redaction import redact_obj
from zf.core.task.kanban_projection import kanban_column_projection
from zf.core.task.store import TaskStore


class BoardTasksChanged(RuntimeError):
    pass


def _identity(state: Path) -> tuple:
    paths = [state / "kanban.json", state / "kanban", *sorted((state / "kanban").glob("*.json"))]
    result = []
    for path in paths:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except FileNotFoundError:
            result.append((str(path), None))
    return tuple(result)


def board_tasks(state_dir: Path, *, project_id: str) -> dict:
    for _ in range(2):
        before = _identity(state_dir)
        store = TaskStore(state_dir / "kanban.json")
        active = store.list_all()
        all_tasks = store.list_all_with_archive()
        ready = {task.id for task in store.ready()}
        if before != _identity(state_dir):
            continue

        def row(task):
            column = kanban_column_projection(task, ready=task.id in ready)
            return {"id": task.id, "title": task.title, "status": task.status,
                    "assigned_to": task.assigned_to or "", "priority": task.priority,
                    "retry_count": task.retry_count, "blocked_reason": task.blocked_reason,
                    "created_at": task.created_at, "phase": None,
                    "blocked_by": list(task.blocked_by), "skills_required": list(task.skills_required),
                    "ready": task.id in ready, "kanban_column": column.column}

        return redact_obj({"schema_version": "board-tasks.v1", "project_id": project_id,
            "source": "TaskStore", "generated_at": datetime.now(timezone.utc).isoformat(),
            "tasks": [row(task) for task in active],
            "archive_tasks": [row(task) for task in all_tasks if task.status in {"done", "cancelled"}],
            "derived_state": "pending"})
    raise BoardTasksChanged("task store changed during read")
