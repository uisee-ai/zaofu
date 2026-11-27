"""Last-moment TaskAttempt lease checks before suspending a role instance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zf.core.state.task_attempts import TaskAttemptStoreError
from zf.runtime.task_attempt_runtime import task_attempt_store


def suspend_lease_blocker(
    runtime: Any, *, instance_id: str, now: float,
) -> tuple[str, dict[str, Any]] | None:
    """Catch leases acquired after idle admission but before Operation.started."""
    try:
        attempts = [
            row["attempt_id"]
            for row in task_attempt_store(runtime).current_rows()
            if row.get("instance_id") == instance_id
            and row.get("status") in {"prepared", "delivering", "sent"}
            and not row.get("unsent_external_gate")
            and (lifecycle_epoch(row.get("lease_expires_at")) or 0) > now
        ]
    except (OSError, TaskAttemptStoreError):
        return "task_attempt_state_unreadable", {}
    if attempts:
        return "current_task_attempt_lease", {"attempt_ids": attempts}
    return None


def lifecycle_epoch(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
