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
    if dispatch_scoped:
        matches = [row for row in sent if row.get("dispatch_id") == meta.get("fanout_context_dispatch_id")
                   and row.get("run_id") == meta.get("fanout_context_trace_id")
                   and row.get("task_id") == meta.get("fanout_context_task_id")]
    else:
        stage = registry.task_stage_bindings().get(meta.get("active_task_stage_binding_key"), {})
        ops = reduce_workflow_operations([e for e in runtime.event_log.read_all() if e.origin == "kernel"])
        matches = [row for row in sent if _session_owns_operation(
            session, role.instance_id, row, ops.get(row.get("operation_id"), {}), stage,
        )]
    if len(matches) != 1:
        return {}, False
    row = matches[0]
    sent_at = iso_epoch(row.get("sent_at", ""))
    if not sent_at or iso_epoch(str(usage.timestamp)) < sent_at:
        return {}, False
    return task_attempt_identity(row), True


def _session_owns_operation(session: str, role: str, row: dict, op: dict, stage: dict) -> bool:
    if (op.get("status") != "running" or op.get("workflow_run_id") != row.get("run_id")
            or op.get("dispatch_id") != row.get("dispatch_id") or op.get("role_instance") != role):
        return False
    if op.get("provider_session_id"):
        return op["provider_session_id"] == session
    return (stage.get("status") == "active" and stage.get("session_id") == session
            and stage.get("current_role_instance") == role and stage.get("workflow_run_id") == row.get("run_id")
            and stage.get("task_id") == row.get("task_id") and stage.get("stage") == op.get("parent_stage_id")
            and stage.get("current_placement_epoch") == row.get("placement_epoch"))
