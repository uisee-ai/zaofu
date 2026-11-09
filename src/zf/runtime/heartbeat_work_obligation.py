"""Confirm on-demand work ownership before treating old heartbeats as stuck."""
from typing import Any

from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.workflow_operation import TERMINAL_OPERATION_STATUSES, reduce_workflow_operations


def on_demand_work_obligation(runtime: Any, role: Any) -> bool | None:
    """None preserves legacy resident/eager behavior; unknown reads keep detection."""
    if role is None or role.lifecycle.mode != "on_demand":
        return None
    instance_id = role.instance_id
    try:
        operations = reduce_workflow_operations(runtime.event_log.read_all())
        if any(row.get("role_instance") == instance_id and row.get("status") == "running"
               for row in operations.values()):
            return True
        for attempt in TaskAttemptStore(runtime.state_dir / "task_attempts.json").current_rows():
            if attempt.get("instance_id") != instance_id or attempt.get("status") not in {
                "prepared", "delivering", "sent",
            }:
                continue
            operation = operations.get(str(attempt.get("operation_id") or ""))
            if operation is not None and (
                operation.get("status") in TERMINAL_OPERATION_STATUSES
                or operation.get("status") == "suspended"
                or operation.get("role_instance") not in ("", instance_id)
            ):
                continue
            return True
        # Probe the store explicitly: the legacy resolver catches read errors.
        runtime.task_store.list_all()
        if runtime._active_task_for_instance(instance_id) is not None:
            return True
        child = runtime._active_fanout_child_for_instance(instance_id)
        if not child:
            return False
        run_id = str(child.get("run_id") or "")
        owners = [row for row in operations.values() if run_id and run_id in {
            row.get("active_attempt_id"), row.get("dispatch_id"),
        }]
        if owners:
            return any(row.get("role_instance") == instance_id and row.get("status") == "running"
                       for row in owners)
        return True  # Legacy fanout with no typed operation remains an obligation.
    except Exception:
        return True


def heartbeat_current_task_still_owned(runtime: Any, registry: Any, instance_id: str) -> bool:
    """Prefer actual provider ownership over a stale worker heartbeat task label."""
    owned = on_demand_work_obligation(runtime, runtime._find_role_by_instance(instance_id))
    if owned is not None:
        return owned
    try:
        _, payload = registry.get_last_heartbeat(instance_id)
    except Exception:
        return True
    if not isinstance(payload, dict):
        return True
    current_task_id = str(payload.get("current_task_id") or payload.get("task_id") or "").strip()
    if not current_task_id:
        return True
    fanout_id = str(payload.get("fanout_id") or "").strip()
    child_id = str(payload.get("child_id") or "").strip()
    if current_task_id.startswith("fanout:") or (fanout_id and child_id):
        try:
            active = runtime._active_fanout_child_for_instance(instance_id)
        except Exception:
            return True
        if not active:
            return False
        if fanout_id and active.get("fanout_id") != fanout_id:
            return False
        if child_id and active.get("child_id") != child_id:
            return False
        return True
    try:
        tasks = runtime.task_store.list_all()
    except Exception:
        return True
    task = next((t for t in tasks if t.id == current_task_id), None)
    if task is None or task.status != "in_progress":
        return False
    try:
        terminal_dispatch = runtime._terminal_task_pipeline_assignments().get((task.id, instance_id))
    except Exception:
        terminal_dispatch = None
    if terminal_dispatch and task.active_dispatch_id in {"", terminal_dispatch}:
        return False
    if (task.assigned_to or "") == instance_id:
        return True
    try:
        latest_dispatched = runtime._latest_dispatched_per_task()
    except Exception:
        latest_dispatched = {}
    return latest_dispatched.get(task.id, "") == instance_id
