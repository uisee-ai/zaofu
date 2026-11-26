"""Bind a disk usage sample to the scheduler attempt that owns its native session."""

from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.backend_session_reader import UsageReport
from zf.runtime.task_attempt_liveness import iso_epoch
from zf.runtime.task_pipeline_attempt_recovery import task_attempt_identity
from zf.runtime.workflow_operation import reduce_workflow_operations


def disk_usage_attempt_binding(runtime, role: RoleConfig, usage: UsageReport | None) -> tuple[dict, bool]:
    """Return identity and whether other resolvers may look up a missing binding."""
    rows = TaskAttemptStore(runtime.state_dir / "task_attempts.json").current_rows()
    if role.instance_id:
        rows = [row for row in rows if row.get("instance_id") == role.instance_id]
    else:
        rows = [row for row in rows if row.get("role") == role.name]
    active = [row for row in rows if row.get("status") in {"prepared", "delivering", "sent"}]
    if usage is None:  # A cost capture probe only asks whether the lane has work.
        return (task_attempt_identity(active[0]), True) if len(active) == 1 else ({}, not active)
    session = getattr(usage, "provider_session_id", "")
    dispatch_scoped = role.role_kind == "reader" and role.lifecycle.reader_context_scope == "dispatch"
    if not session and (dispatch_scoped or getattr(usage, "usage_series_id", "") or getattr(usage, "source_request_id", "")):
        return {}, False
    if not rows:
        return {}, not dispatch_scoped
    if not session and not dispatch_scoped:
        if not active:
            return {}, True
        if len(active) == 1 and active[0].get("status") == "sent":
            return task_attempt_identity(active[0]), True
        return {}, False
    # A claimed send has no proof of provider delivery. An old claim must never
    # become the sole candidate merely because its replacement finished.
    sent = [row for row in active if row.get("status") == "sent"]
    if not sent or not session:
        return {}, False
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", project_root=str(runtime.state_dir.parent))
    if str(registry.get(role.instance_id) or "") != session:
        return {}, False
    meta = registry.instance_meta().get(role.instance_id, {})
    events = [e for e in runtime.event_log.read_all() if e.origin == "kernel"]
    ops = reduce_workflow_operations(events)
    stage = registry.task_stage_bindings().get(meta.get("active_task_stage_binding_key"), {})
    keys = {(row.get("operation_id"), row.get("dispatch_id")) for row in sent}
    dispatches = {}
    hook = None
    if role.backend == "codex" and role.role_kind == "reader":
        for event in events:
            p = event.payload
            if event.type == "codex.hook.user_prompt_submit" and event.actor == role.instance_id:
                hook = event
            elif event.type in {"fanout.child.dispatched", "fanout.synth.dispatched"}:
                key = (p.get("operation_id"), p.get("dispatch_id") or p.get("run_id"))
                if key in keys and p.get("role_instance") == role.instance_id:
                    dispatches[key] = event
    matches = []
    for row in sent:
        op = ops.get(row.get("operation_id"), {})
        dispatch = dispatches.get((row.get("operation_id"), row.get("dispatch_id")))
        if dispatch:
            owned = (_fanout_owns_physical_send(events, dispatch, row, op, meta, role.instance_id)
                     and _fanout_owns_usage(
                         usage, dispatch, hook, row, op, meta, ops, rows,
                         exclusive_candidate=dispatch_scoped and len(active) == 1,
                     ))
        elif dispatch_scoped:
            owned = _context_owns_attempt(meta, row)
        else:
            owned = _session_owns_operation(session, role.instance_id, row, op, stage)
        if owned:
            matches.append(row)
    if len(matches) != 1:
        return {}, False
    row = matches[0]
    sent_at = iso_epoch(row.get("sent_at", ""))
    if not sent_at or iso_epoch(str(usage.timestamp)) < sent_at:
        return {}, False
    return task_attempt_identity(row), True


def _fanout_owns_usage(usage, dispatch, hook, row, op, meta, ops, rows, *, exclusive_candidate: bool) -> bool:
    session = usage.provider_session_id
    if op.get("provider_session_id") not in ("", session):
        return False
    if hook is not None and hook.payload.get("session_id") != session and iso_epoch(hook.ts) < iso_epoch(dispatch.ts):
        hook = None  # An earlier session ended before this physical delivery.
    if hook is not None:
        if (hook.causation_id != dispatch.id or hook.payload.get("session_id") != session
                or not 0 < iso_epoch(hook.ts) <= iso_epoch(str(usage.timestamp))):
            return False
        if usage.provider_usage_turn_id:
            return hook.payload.get("turn_id") == usage.provider_usage_turn_id

    # With no scoped turn/hook pair, only the sole native turn of an
    # independently registered, exclusive first delivery can be attributed.
    first = usage.registered_first_turn
    if (not exclusive_candidate or not first or first[1] < iso_epoch(row.get("sent_at", ""))
            or (hook is not None and hook.payload.get("turn_id") != first[0])
            or dispatch.payload.get("retry_of_run_id") or op.get("redrive_count")
            or not _context_owns_attempt(meta, row) or op.get("provider_session_id") != session):
        return False
    if any(other["operation_id"] != op["operation_id"] and other.get("provider_session_id") == session
           for other in ops.values()):
        return False
    return not any(other.get("operation_id") == row["operation_id"] and other["attempt_id"] != row["attempt_id"]
                   for other in rows)


def _fanout_owns_physical_send(events, dispatch, row: dict, op: dict, meta: dict, role: str) -> bool:
    """Join the registered WRC and hook to the physical delivery receipt.

    Fresh synth occurrences carry the logical attempt, while redrives may
    carry the scheduler attempt. The physical send identifies the latter.
    """
    p = dispatch.payload
    key = row.get("dispatch_id")
    task = row.get("task_id")
    if (not task or not key or not _running_role_operation(op, row, role)
            or op.get("task_id") != task or p.get("operation_id") != row.get("operation_id")
            or p.get("workflow_run_id") != row.get("run_id")
            or (p.get("dispatch_id") or p.get("run_id")) != key
            or meta.get("fanout_context_dispatch_id") != key
            or meta.get("fanout_context_trace_id") != row.get("run_id")
            or any(value and value != task for value in (
                dispatch.task_id, p.get("task_id"), meta.get("fanout_context_task_id")))
            or (p.get("attempt_id") and p["attempt_id"] not in {row.get("attempt_id"), op.get("active_attempt_id")})
            or (p.get("lease_id") and p["lease_id"] not in {row.get("lease_id"), op.get("lease_id")})):
        return False
    for event in reversed(events):
        if event.type != "task.attempt.sent":
            continue
        receipt = event.payload
        if receipt.get("operation_id") == p["operation_id"] and receipt.get("dispatch_id") == key:
            return (receipt.get("run_id") == row.get("run_id") and receipt.get("role_instance") == role
                    and receipt.get("task_id") == task and receipt.get("attempt_id") == row.get("attempt_id")
                    and receipt.get("lease_id") == row.get("lease_id") and event.ts <= dispatch.ts)
    return False


def _context_owns_attempt(meta: dict, row: dict) -> bool:
    return (row.get("dispatch_id") == meta.get("fanout_context_dispatch_id")
            and row.get("run_id") == meta.get("fanout_context_trace_id")
            and row.get("task_id") == meta.get("fanout_context_task_id"))


def _running_role_operation(op: dict, row: dict, role: str) -> bool:
    return (op.get("status") == "running" and op.get("workflow_run_id") == row.get("run_id")
            and op.get("dispatch_id") == row.get("dispatch_id") and op.get("role_instance") == role)


def _session_owns_operation(session: str, role: str, row: dict, op: dict, stage: dict) -> bool:
    if not _running_role_operation(op, row, role):
        return False
    if op.get("provider_session_id"):
        return op["provider_session_id"] == session
    return (stage.get("status") == "active" and stage.get("session_id") == session
            and stage.get("current_role_instance") == role and stage.get("workflow_run_id") == row.get("run_id")
            and stage.get("task_id") == row.get("task_id") and stage.get("stage") == op.get("parent_stage_id")
            and stage.get("current_placement_epoch") == row.get("placement_epoch"))
