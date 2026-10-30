"""Cancelling stale operations must not block a parent with live sibling work."""

from pathlib import Path

import pytest

from tests.test_workflow_operation_resume import _config, _seed_live_retry
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume


@pytest.mark.parametrize("sibling", ["parent", "child", "foreign_run", "foreign_parent", "expired", "none"])
def test_stale_operation_cleanup_preserves_only_matching_live_parent(tmp_path: Path, sibling: str):
    state = tmp_path / "state"
    state.mkdir()
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="FLOW-1", status="in_progress"))
    log = EventLog(state / "events.jsonl")
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log))

    def start(operation_id, *, run="run-1", task="FLOW-1", parent="FLOW-1"):
        ensured = service.ensure_operation(
            workflow_run_id=run, operation_id=operation_id, operation_type="agent",
            request={"prompt": "plan"}, task_id=task, parent_task_id=parent,
        )
        service.mark_started(
            operation_id=operation_id, request_hash=ensured.request_hash,
            workflow_run_id=run, task_id=task, dispatch_id=f"dispatch-{operation_id}",
        )

    start("old")
    if sibling != "none":
        start("new", run="other-run" if sibling == "foreign_run" else "run-1",
              task="CHILD" if sibling == "child" else "FLOW-1",
              parent="OTHER" if sibling == "foreign_parent" else "FLOW-1")
        _seed_live_retry(state, operation_id="new", task_id="CHILD" if sibling == "child" else "FLOW-1")
        if sibling == "expired":
            attempts = TaskAttemptStore(state / "task_attempts.json")
            row = next(row for row in attempts.current_rows() if row["operation_id"] == "new")
            attempts.renew_lease(row["attempt_id"], updated_at="2000-01-01T00:00:00+00:00",
                                 lease_expires_at="2000-01-01T00:00:00+00:00")
    projection = build_workflow_resume_projection(state, _config())
    checkpoint = next(row for row in projection["operation_checkpoints"] if row["operation_id"] == "old")
    assert checkpoint["safe_resume_action"] == "cancel_stale_operation"
    result = apply_workflow_resume(state, _config(), checkpoint_id=checkpoint["checkpoint_id"])
    assert result["applied"] == 1
    assert reduce_workflow_operations(log.read_all())["old"]["status"] == "cancelled"
    parent = store.get("FLOW-1")
    if sibling in {"parent", "child"}:
        assert parent.status == "in_progress"
        assert parent.blocked_reason == ""
        assert not any(e.type == "task.status_changed" for e in log.read_all())
    else:
        assert parent.status == "blocked"
        assert parent.blocked_reason == "stale_workflow_operation:old"
