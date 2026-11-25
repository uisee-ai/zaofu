"""Defer a Reader recovery timeout only while its leased replacement makes progress."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.events import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore, TaskAttemptStoreError
from zf.runtime.attempt_read_progress import gained_required_read_coverage
from zf.runtime.pause_lifecycle import active_request_age_seconds
from zf.runtime.provider_turn_liveness import _parse_event_ts
from zf.runtime.reader_rebuild_admission import reader_rebuild_dispatch_matches
from zf.runtime.workflow_operation import reduce_workflow_operations


def progressing_rebuilt_reader(
    state_dir: Path, events: list[ZfEvent], action: dict, timeout: int,
) -> bool:
    dispatch = next((e for e in reversed(events) if e.origin == "kernel"
                     and e.type == "fanout.child.dispatched"
                     and e.payload.get("fanout_id") == action.get("fanout_id")
                     and e.payload.get("child_id") == action.get("fanout_child_id")), None)
    if not dispatch or not reader_rebuild_dispatch_matches(dispatch, action):
        return False
    p = dispatch.payload
    call = p.get("payload") or {}
    op = reduce_workflow_operations(events).get(p["operation_id"], {})
    if (op.get("status") != "running" or op.get("operation_type") != "fanout_reader_child"
            or not call.get("request_hash") or op.get("request_hash") != call["request_hash"]
            or not call.get("attempt_id") or op.get("active_attempt_id") != call["attempt_id"]
            or op.get("role_instance") != p.get("role_instance")
            or op.get("workflow_run_id") != action["workflow_run_id"]):
        return False
    try:
        row = TaskAttemptStore(state_dir / "task_attempts.json").current_for_attempt(
            task_id=op.get("task_id") or "", attempt_id=p.get("attempt_id") or "",
        ) or {}
    except TaskAttemptStoreError:
        return False
    bindings = {"attempt_id": p.get("attempt_id"), "lease_id": p.get("lease_id"),
                "operation_id": p["operation_id"], "dispatch_id": p.get("dispatch_id"),
                "instance_id": p.get("role_instance"), "run_id": action["workflow_run_id"]}
    now = datetime.now(timezone.utc)
    expires = _parse_event_ts(row.get("lease_expires_at"))
    if (row.get("status") != "sent" or not expires or expires <= now
            or any(not value or row.get(key) != value for key, value in bindings.items())):
        return False
    since = now - timedelta(seconds=timeout)
    issued = _parse_event_ts(dispatch.ts)
    if issued and issued <= now and active_request_age_seconds(
        events, created_at=issued, now=now, run_id=action["workflow_run_id"],
    ) < timeout:
        return True  # The new consumer has its own initial response window.
    return gained_required_read_coverage(
        state_dir, events, attempt_id=call["attempt_id"], operation_id=p["operation_id"],
        run_id=action["workflow_run_id"], actor=p["role_instance"], dispatch_id=p["dispatch_id"],
        since=since, now=now,
    )
