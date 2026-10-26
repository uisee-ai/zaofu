"""Read canonical attempt identity for recovery consumers; never grant a lease."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.state.task_attempts import TaskAttemptStore, TaskAttemptStoreError


def recovery_attempt_facts(
    state_dir: Path, task: Any, *, run_id: str, dispatch_id: str = "",
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "status": "unknown", "identity": {}, "authorizes_continuation": False,
        "source_ref": "task_attempts.json",
        "reason": "canonical Run and active dispatch are required",
    }
    active_dispatch = str(getattr(task, "active_dispatch_id", "") or "")
    if task is None or not run_id or not active_dispatch:
        return facts
    if dispatch_id and dispatch_id != active_dispatch:
        facts["reason"] = "requested dispatch differs from canonical active dispatch"
        return facts
    try:
        attempt = TaskAttemptStore(Path(state_dir) / "task_attempts.json").current(
            run_id=run_id, task_id=task.id,
        )
    except (OSError, ValueError, TaskAttemptStoreError):
        facts["reason"] = "canonical attempt store is unreadable"
        return facts
    if attempt is None:
        facts["reason"] = "current Run/task attempt is missing or ambiguous"
        return facts
    if attempt.get("dispatch_id") != active_dispatch:
        facts["reason"] = "current attempt does not match canonical active dispatch"
        return facts
    facts.update({
        "status": "bound",
        "reason": "observed canonical identity; liveness, lease and controlled action require revalidation",
        "identity": {key: attempt.get(key) for key in (
            "run_id", "task_id", "operation_id", "attempt_id", "lease_id",
            "dispatch_id", "role", "instance_id", "identity_version", "placement_epoch",
            "ordinal", "series", "status", "lease_expires_at", "terminal_event_id",
        )},
    })
    return facts
