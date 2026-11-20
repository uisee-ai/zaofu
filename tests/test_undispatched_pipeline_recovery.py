from pathlib import Path
import json

import pytest

from zf.core.config.schema import ProjectConfig, WorkflowConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_operation_resume_checkpoints
from zf.runtime.workflow_resume_apply import apply_workflow_resume


def seed(root: Path):
    (root / "kanban.json").write_text("[]")
    log = EventLog(root / "events.jsonl")
    service = WorkflowOperationService(state_dir=root, event_log=log, event_writer=EventWriter(log))
    request = {"task_pipeline_stage": "impl", "operation_generation": 1}
    ensured = service.ensure_operation(
        workflow_run_id="run-1", operation_id="op-1", operation_type="task-stage",
        request=request, task_id="T1", active_attempt_id="planned-attempt",
        lease_id="planned-lease",
    )
    return log, service, ensured


def cancel(service, ensured, reason="workflow_resume_cancelled_stale_operation"):
    service.cancel(operation_id="op-1", request_hash=ensured.request_hash,
                   workflow_run_id="run-1", task_id="T1", reason=reason)


def test_pending_pipeline_request_without_attempt_is_left_for_scheduler(tmp_path):
    seed(tmp_path)
    rows = build_workflow_operation_resume_checkpoints(tmp_path)
    assert len(rows) == 1
    assert rows[0].safe_resume_action == "no_action"


def test_resume_recovers_only_never_dispatched_stale_cancellation(tmp_path):
    log, service, ensured = seed(tmp_path)
    cancel(service, ensured)
    rows = build_workflow_operation_resume_checkpoints(tmp_path)
    assert len(rows) == 1
    assert rows[0].safe_resume_action == "redrive_undispatched_task_pipeline_operation"
    config = ZfConfig(project=ProjectConfig(name="recovery"), workflow=WorkflowConfig())
    result = apply_workflow_resume(tmp_path, config, checkpoint_id=rows[0].checkpoint_id)
    assert result["applied"] == 1
    operation = reduce_workflow_operations(log.read_all())["op-1"]
    assert operation["status"] == "requested"
    assert operation["operation_generation"] == 1
    assert operation["request_hash"] == ensured.request_hash
    assert operation["redrive_count"] == 1
    assert not (tmp_path / "task_attempts.json").exists()
    assert (tmp_path / "kanban.json").read_text() == "[]"
    assert apply_workflow_resume(tmp_path, config)["applied"] == 0


@pytest.mark.parametrize("history", ["started", "attempt", "operator_cancel", "corrupt"])
def test_undispatched_recovery_does_not_reopen_other_cancellations(tmp_path, history):
    log, service, ensured = seed(tmp_path)
    if history == "started":
        service.mark_started(operation_id="op-1", request_hash=ensured.request_hash,
                             workflow_run_id="run-1", task_id="T1", dispatch_id="dispatch-1")
    if history == "attempt":
        (tmp_path / "task_attempts.json").write_text(json.dumps({"attempts": {
            "old-attempt": {"operation_id": "op-1", "status": "expired"},
        }}))
    if history == "corrupt":
        (tmp_path / "task_attempts.json").write_text("not-json")
    cancel(service, ensured, "owner_cancelled" if history == "operator_cancel"
           else "workflow_resume_cancelled_stale_operation")
    assert build_workflow_operation_resume_checkpoints(tmp_path) == []
    assert reduce_workflow_operations(log.read_all())["op-1"]["status"] == "cancelled"


def test_late_attempt_prevents_applying_stale_recovery_checkpoint(tmp_path):
    log, service, ensured = seed(tmp_path)
    cancel(service, ensured)
    rows = build_workflow_operation_resume_checkpoints(tmp_path)
    assert rows
    (tmp_path / "task_attempts.json").write_text(json.dumps({"attempts": {
        "late": {"operation_id": "op-1", "status": "expired"},
    }}))
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_resume_apply import _apply_operation_checkpoint
    result = _apply_operation_checkpoint(TaskStore(tmp_path / "kanban.json"), EventWriter(log),
                                         rows[0], state_dir=tmp_path)
    assert not result.applied
    assert reduce_workflow_operations(log.read_all())["op-1"]["status"] == "cancelled"


def test_service_requires_matching_controlled_recovery_decision(tmp_path):
    log, service, ensured = seed(tmp_path)
    cancel(service, ensured)
    from zf.runtime.workflow_operation import WorkflowOperationError
    with pytest.raises(WorkflowOperationError, match="suspended operation"):
        service.admit_redrive(
            operation_id="op-1", request_hash=ensured.request_hash,
            workflow_run_id="run-1", task_id="T1", source_attempt_id="",
            recovery_decision_event_id="invented", recovery_decision_owner="kernel_replay",
            reason="retry",
        )
    assert reduce_workflow_operations(log.read_all())["op-1"]["status"] == "cancelled"


@pytest.mark.parametrize("event_type", ["task.attempt.started", "task.dispatched"])
def test_historical_delivery_ledger_blocks_recovery_without_attempt_store(tmp_path, event_type):
    from zf.core.events.model import ZfEvent
    log, service, ensured = seed(tmp_path)
    EventWriter(log).append(ZfEvent(
        type=event_type, actor="kernel", origin="kernel", task_id="T1",
        payload={"operation_id": "op-1", "attempt_id": "planned-attempt"},
    ))
    cancel(service, ensured)
    assert not (tmp_path / "task_attempts.json").exists()
    assert build_workflow_operation_resume_checkpoints(tmp_path) == []
