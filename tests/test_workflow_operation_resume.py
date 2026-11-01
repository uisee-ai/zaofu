from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, WorkflowConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.store import TaskStore
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)
from zf.runtime.workflow_resume import (
    WorkflowOperationResumeCheckpoint,
    build_workflow_resume_projection,
)
from zf.runtime.workflow_resume_apply import (
    _apply_operation_checkpoint,
    apply_workflow_resume,
)


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="operation-resume"),
        workflow=WorkflowConfig(),
    )


def _seed_live_retry(
    state_dir: Path,
    *,
    operation_id: str,
    task_id: str,
) -> None:
    store = TaskAttemptStore(state_dir / "task_attempts.json")
    ensured = store.ensure_for_dispatch(
        run_id="run-1",
        task_id=task_id,
        dispatch_id="dispatch-retry-1",
        role="verify",
        instance_id="verify-1",
        operation_id=operation_id,
        briefing_ref="briefings/retry.md",
        created_at="2099-01-01T00:00:00+00:00",
        lease_expires_at="2100-01-01T00:00:00+00:00",
        max_attempts=3,
    )
    store.claim_delivery(
        ensured.attempt["attempt_id"],
        updated_at="2099-01-01T00:00:01+00:00",
    )
    store.mark_sent(
        ensured.attempt["attempt_id"],
        updated_at="2099-01-01T00:00:02+00:00",
    )


def _seed_successful_task_pipeline_operation(
    service: WorkflowOperationService,
    *,
    operation_id: str,
    task_id: str,
    stage: str,
    task_map_generation: str,
) -> None:
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="task-stage",
        request={
            "task_pipeline_stage": stage,
            "task_map_generation": task_map_generation,
            "operation_generation": 1,
        },
        task_id=task_id,
    )
    service.mark_started(
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id=task_id,
        dispatch_id=f"dispatch-{operation_id}",
    )
    service.event_writer.append(ZfEvent(
        type="workflow.call.result.admitted",
        actor="zf-cli",
        task_id=task_id,
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": operation_id,
            "request_hash": ensured.request_hash,
            "semantic_verdict": "passed",
            "control_result_ref": {},
        },
    ))
    service.settle(
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id=task_id,
        admitted_call_result_ref={
            "ref": f"artifacts/call-results/{operation_id}.json",
            "sha256": "a" * 64,
        },
    )


def test_interrupted_operation_dry_run_then_cancel_is_idempotent(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-1",
        operation_type="agent",
        request={"prompt": "plan"},
        task_id="FLOW-1",
        parent_task_id="FLOW-1",
    )
    service.mark_started(
        operation_id="op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        dispatch_id="dispatch-1",
    )
    service.interrupt(
        operation_id="op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        reason="graceful_stop",
    )

    projection = build_workflow_resume_projection(state_dir, _config())
    checkpoint = projection["operation_checkpoints"][0]
    assert projection["summary"]["resumable_operations"] == 1
    assert projection["summary"]["operation_pending"] == 1
    assert checkpoint["safe_resume_action"] == "cancel_interrupted_operation"
    dry_run = apply_workflow_resume(
        state_dir,
        _config(),
        dry_run=True,
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert dry_run["applied"] == 0
    assert dry_run["operation_results"][0]["reason"] == "dry run"

    applied = apply_workflow_resume(
        state_dir,
        _config(),
        checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert applied["applied"] == 1
    assert reduce_workflow_operations(log.read_all())["op-1"]["status"] == "cancelled"
    second = apply_workflow_resume(state_dir, _config())
    assert second["applied"] == 0
    assert second["projection"]["summary"]["resumable_operations"] == 0


def test_interrupted_operation_with_newer_live_retry_is_not_actionable(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-race",
        operation_type="fanout_reader_child",
        request={"prompt": "verify"},
        task_id="FLOW-1",
        parent_task_id="FLOW-1",
    )
    service.mark_started(
        operation_id="op-race",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        dispatch_id="dispatch-1",
    )
    service.interrupt(
        operation_id="op-race",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        reason="graceful_stop",
    )
    _seed_live_retry(
        state_dir,
        operation_id="op-race",
        task_id="FLOW-1",
    )

    projection = build_workflow_resume_projection(state_dir, _config())

    assert projection["summary"]["operation_pending"] == 0
    checkpoint = projection["operation_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == "no_action"
    assert checkpoint["reason"] == (
        "operation has a newer live TaskAttempt after interruption"
    )


def test_operation_resume_apply_rechecks_new_live_retry_before_cancel(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-cas",
        operation_type="fanout_reader_child",
        request={"prompt": "verify"},
        task_id="FLOW-1",
        parent_task_id="FLOW-1",
    )
    service.mark_started(
        operation_id="op-cas",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        dispatch_id="dispatch-1",
    )
    service.interrupt(
        operation_id="op-cas",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        reason="graceful_stop",
    )
    stale = WorkflowOperationResumeCheckpoint(**(
        build_workflow_resume_projection(state_dir, _config())[
            "operation_checkpoints"
        ][0]
    ))
    _seed_live_retry(
        state_dir,
        operation_id="op-cas",
        task_id="FLOW-1",
    )

    result = _apply_operation_checkpoint(
        TaskStore(state_dir / "kanban.json"),
        writer,
        stale,
        state_dir=state_dir,
    )

    assert result.applied is False
    assert result.reason == (
        "operation has a newer live TaskAttempt; stale resume action was not "
        "applied"
    )
    assert reduce_workflow_operations(log.read_all())["op-cas"]["status"] == (
        "suspended"
    )
    assert not any(
        event.type == "workflow.operation.cancelled"
        for event in log.read_all()
    )


@pytest.mark.parametrize(
    "interruption_reason",
    ["graceful_stop", "task_pipeline_attempt_lease_expired"],
)
def test_interrupted_task_pipeline_operation_redrives_after_restart(
    tmp_path: Path,
    interruption_reason: str,
) -> None:
    state_dir = tmp_path / interruption_reason / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-1-impl",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 2,
        },
        task_id="TASK-1",
    )
    service.mark_started(
        operation_id="op-task-1-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-1",
        dispatch_id="dispatch-1",
        active_attempt_id="attempt-1",
    )
    service.interrupt(
        operation_id="op-task-1-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-1",
        reason=interruption_reason,
        source_attempt_id="attempt-1",
    )

    projection = build_workflow_resume_projection(state_dir, _config())
    checkpoint = projection["operation_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == (
        "redrive_interrupted_task_pipeline_operation"
    )

    applied = apply_workflow_resume(
        state_dir,
        _config(),
        checkpoint_id=checkpoint["checkpoint_id"],
    )

    assert applied["applied"] == 1
    operation = reduce_workflow_operations(log.read_all())["op-task-1-impl"]
    assert operation["status"] == "requested"
    assert operation["redrive_count"] == 1
    assert operation["redrive_source_attempt_ids"] == ["attempt-1"]
    redrive = next(
        event for event in log.read_all()
        if event.type == "workflow.operation.redrive_admitted"
    )
    assert redrive.payload["recovery_decision_owner"] == "kernel_replay"


def test_later_successful_task_pipeline_stage_suppresses_old_interruption(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    old = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-old-verify",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "verify",
            "task_map_generation": "generation-old",
            "operation_generation": 1,
        },
        task_id="TASK-AUDIT",
    )
    service.mark_started(
        operation_id="op-old-verify",
        request_hash=old.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-AUDIT",
        dispatch_id="dispatch-old",
    )
    service.interrupt(
        operation_id="op-old-verify",
        request_hash=old.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-AUDIT",
        reason="task_pipeline_attempt_lease_expired",
    )
    _seed_successful_task_pipeline_operation(
        service,
        operation_id="op-new-verify",
        task_id="TASK-AUDIT",
        stage="verify",
        task_map_generation="generation-new",
    )

    projection = build_workflow_resume_projection(state_dir, _config())

    assert projection["summary"]["operation_pending"] == 0
    assert projection["summary"]["resumable_operations"] == 0
    assert projection["operation_checkpoints"] == []


def test_operation_resume_apply_rechecks_later_task_pipeline_success(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    old = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-old-verify",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "verify",
            "task_map_generation": "generation-old",
            "operation_generation": 1,
        },
        task_id="TASK-AUDIT",
    )
    service.mark_started(
        operation_id="op-old-verify",
        request_hash=old.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-AUDIT",
        dispatch_id="dispatch-old",
    )
    service.interrupt(
        operation_id="op-old-verify",
        request_hash=old.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-AUDIT",
        reason="task_pipeline_attempt_lease_expired",
    )
    stale = WorkflowOperationResumeCheckpoint(**(
        build_workflow_resume_projection(state_dir, _config())[
            "operation_checkpoints"
        ][0]
    ))
    _seed_successful_task_pipeline_operation(
        service,
        operation_id="op-new-verify",
        task_id="TASK-AUDIT",
        stage="verify",
        task_map_generation="generation-new",
    )

    result = _apply_operation_checkpoint(
        TaskStore(state_dir / "kanban.json"),
        writer,
        stale,
        state_dir=state_dir,
    )

    assert result.applied is False
    assert result.reason == (
        "operation was superseded by later successful Task Pipeline operation "
        "op-new-verify"
    )
    assert reduce_workflow_operations(log.read_all())[
        "op-old-verify"
    ]["status"] == "suspended"
    assert not any(
        event.type == "workflow.operation.redrive_admitted"
        for event in log.read_all()
    )


def test_legacy_cancelled_restart_operation_gets_bounded_redrive(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=EventWriter(log),
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-1-impl",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 2,
        },
        task_id="TASK-1",
    )
    service.mark_started(
        operation_id="op-task-1-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-1",
        dispatch_id="dispatch-1",
        active_attempt_id="attempt-1",
    )
    interrupted = service.interrupt(
        operation_id="op-task-1-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-1",
        reason="graceful_stop",
        source_attempt_id="attempt-1",
    )
    service.cancel(
        operation_id="op-task-1-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-1",
        reason="workflow_resume_cancelled_interrupted_operation",
        causation_id=interrupted.id if interrupted is not None else "",
    )

    projection = build_workflow_resume_projection(state_dir, _config())
    checkpoint = projection["operation_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == (
        "redrive_cancelled_interrupted_task_pipeline_operation"
    )

    applied = apply_workflow_resume(
        state_dir,
        _config(),
        checkpoint_id=checkpoint["checkpoint_id"],
    )

    assert applied["applied"] == 1
    operation = reduce_workflow_operations(log.read_all())["op-task-1-impl"]
    assert operation["status"] == "requested"
    assert operation["redrive_count"] == 1


def _seed_budget_blocked_task_pipeline_operation(
    tmp_path: Path,
    *,
    dimension: str = "tokens",
) -> tuple[Path, EventLog, str]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(type="run.goal.started", actor="operator", correlation_id="run-budget-1", payload={"run_id": "run-budget-1"}))
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-budget-1",
        operation_id="op-budget-impl",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 1,
            "operation_limits": {"max_usage_samples": 60, "cost_budget_usd": 8},
        },
        task_id="TASK-BUDGET",
    )
    service.mark_started(
        operation_id="op-budget-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-budget-1",
        task_id="TASK-BUDGET",
        dispatch_id="dispatch-budget-1",
        active_attempt_id="attempt-budget-1",
    )
    exceeded = writer.append(ZfEvent(
        type="workflow.budget.exceeded",
        actor="zf-cli",
        task_id="TASK-BUDGET",
        correlation_id="run-budget-1",
        payload={
            "scope": "operation",
            "scope_id": "op-budget-impl",
            "workflow_run_id": "run-budget-1",
            "exceeded_dimensions": [dimension],
            "measurement": {"total_tokens": 120, "usage_samples": 60},
        },
    ))
    service.block(
        operation_id="op-budget-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-budget-1",
        task_id="TASK-BUDGET",
        reason=f"workflow_budget_exceeded:{dimension}",
        causation_id=exceeded.id,
        correlation_id="run-budget-1",
        details={
            "budget_scope": "operation",
            "budget_scope_id": "op-budget-impl",
            "exceeded_dimensions": [dimension],
        },
    )
    return state_dir, log, ensured.request_hash


@pytest.mark.parametrize("amended_limit", [0, 120])
def test_sample_budget_amendment_is_scoped_and_enforced_after_redrive(tmp_path, monkeypatch, amended_limit):
    from argparse import Namespace
    from zf.cli.goal import _run_set
    from zf.runtime.workflow_budget_guard import _operation_limits, _exceeded_dimensions

    state_dir, log, _ = _seed_budget_blocked_task_pipeline_operation(tmp_path, dimension="usage_samples")
    monkeypatch.setattr("zf.cli.goal._context", lambda _args: Namespace(state_dir=state_dir, config=_config()))
    args = Namespace(objective="", status="active", reason="owner approved", state_dir=None,
                     operation="op-budget-impl", operation_max_usage_samples=60)
    assert _run_set(args) == 1
    assert build_workflow_resume_projection(state_dir, _config())["operation_checkpoints"] == []
    args.operation_max_usage_samples = -1
    assert _run_set(args) == 2
    args.operation = "other-operation"
    args.operation_max_usage_samples = amended_limit
    assert _run_set(args) == 1
    args.operation = "op-budget-impl"
    args.operation_max_usage_samples = amended_limit
    assert _run_set(args) == 0
    before_redrive = log.read_all()
    blocked_operation = reduce_workflow_operations(before_redrive)["op-budget-impl"]
    assert _operation_limits(state_dir, blocked_operation, events=before_redrive)["max_usage_samples"] == 60
    checkpoint = build_workflow_resume_projection(state_dir, _config())["operation_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == "redrive_budget_amended_task_pipeline_operation"
    assert apply_workflow_resume(state_dir, _config(), checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    events = log.read_all()
    operation = reduce_workflow_operations(events)["op-budget-impl"]
    limits = _operation_limits(state_dir, operation, events=events)
    assert limits["max_usage_samples"] == amended_limit
    assert limits["cost_budget_usd"] == 8
    assert "usage_samples" not in _exceeded_dimensions(limits, {"meter_available": True, "usage_samples": 119})
    if amended_limit:
        assert "usage_samples" in _exceeded_dimensions(limits, {"meter_available": True, "usage_samples": 120})
    else:
        assert "usage_samples" not in _exceeded_dimensions(limits, {"meter_available": True, "usage_samples": 10000})


def test_sample_amendment_can_cover_only_the_current_run(tmp_path, monkeypatch):
    from argparse import Namespace
    from zf.cli.goal import _run_set
    from zf.runtime.workflow_budget_guard import _operation_limits

    state_dir, log, _ = _seed_budget_blocked_task_pipeline_operation(tmp_path, dimension="usage_samples")
    monkeypatch.setattr("zf.cli.goal._context", lambda _args: Namespace(state_dir=state_dir, config=_config()))
    args = Namespace(objective="", status="active", reason="owner disabled sample cap", state_dir=None,
                     operation="op-budget-impl", operation_max_usage_samples=0, all_run_operations=True)
    assert _run_set(args) == 0
    checkpoint = build_workflow_resume_projection(state_dir, _config())["operation_checkpoints"][0]
    assert apply_workflow_resume(state_dir, _config(), checkpoint_id=checkpoint["checkpoint_id"])["applied"] == 1
    service = WorkflowOperationService(state_dir=state_dir, event_log=log, event_writer=EventWriter(log))
    for run_id, operation_id in [("run-budget-1", "op-same-run"), ("run-other", "op-other-run")]:
        service.ensure_operation(workflow_run_id=run_id, operation_id=operation_id,
                                 operation_type="task-stage", task_id="another-task",
                                 request={"operation_limits": {"max_usage_samples": 60, "cost_budget_usd": 8}})
    events = log.read_all()
    operations = reduce_workflow_operations(events)
    assert _operation_limits(state_dir, operations["op-same-run"], events=events)["max_usage_samples"] == 0
    assert _operation_limits(state_dir, operations["op-other-run"], events=events)["max_usage_samples"] == 60
    assert _operation_limits(state_dir, operations["op-same-run"], events=events)["cost_budget_usd"] == 8


def test_budget_blocked_task_pipeline_redrives_after_owner_amendment(
    tmp_path: Path,
) -> None:
    state_dir, log, _request_hash = (
        _seed_budget_blocked_task_pipeline_operation(tmp_path)
    )
    amendment = EventWriter(log).append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        correlation_id="run-budget-1",
        payload={
            "source": "zf_goal_cli",
            "run_id": "run-budget-1",
            "workflow_run_id": "run-budget-1",
            "status": "active",
            "run_limits_patch": {"token_budget": 0},
        },
    ))

    projection = build_workflow_resume_projection(state_dir, _config())
    checkpoint = projection["operation_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == (
        "redrive_budget_amended_task_pipeline_operation"
    )
    assert checkpoint["recovery_decision_event_id"] == amendment.id

    applied = apply_workflow_resume(
        state_dir,
        _config(),
        checkpoint_id=checkpoint["checkpoint_id"],
    )

    assert applied["applied"] == 1
    operation = reduce_workflow_operations(log.read_all())["op-budget-impl"]
    assert operation["status"] == "requested"
    assert operation["redrive_count"] == 1
    redrive = next(
        event
        for event in log.read_all()
        if event.type == "workflow.operation.redrive_admitted"
    )
    assert redrive.payload["recovery_decision_owner"] == (
        "operator_budget_amendment"
    )
    assert redrive.payload["recovery_decision_event_id"] == amendment.id


def test_budget_blocked_operation_rejects_wrong_run_or_partial_amendment(
    tmp_path: Path,
) -> None:
    state_dir, log, _request_hash = (
        _seed_budget_blocked_task_pipeline_operation(tmp_path)
    )
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        correlation_id="run-other",
        payload={
            "source": "zf_goal_cli",
            "run_id": "run-other",
            "workflow_run_id": "run-other",
            "status": "active",
            "run_limits_patch": {"token_budget": 0},
        },
    ))
    writer.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        correlation_id="run-budget-1",
        payload={
            "source": "zf_goal_cli",
            "run_id": "run-budget-1",
            "workflow_run_id": "run-budget-1",
            "status": "active",
            "run_limits_patch": {"cost_budget_usd": 1000},
        },
    ))

    projection = build_workflow_resume_projection(state_dir, _config())

    assert projection["summary"]["operation_pending"] == 0
    assert projection["operation_checkpoints"] == []


def test_budget_amendment_before_block_does_not_authorize_redrive(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        correlation_id="run-budget-1",
        payload={
            "source": "zf_goal_cli",
            "run_id": "run-budget-1",
            "workflow_run_id": "run-budget-1",
            "status": "active",
            "run_limits_patch": {"token_budget": 0},
        },
    ))
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-budget-1",
        operation_id="op-budget-impl",
        operation_type="task-stage",
        request={"task_pipeline_stage": "impl"},
        task_id="TASK-BUDGET",
    )
    exceeded = writer.append(ZfEvent(
        type="workflow.budget.exceeded",
        actor="zf-cli",
        correlation_id="run-budget-1",
        payload={
            "scope": "operation",
            "scope_id": "op-budget-impl",
            "workflow_run_id": "run-budget-1",
            "exceeded_dimensions": ["tokens"],
            "measurement": {"total_tokens": 120},
        },
    ))
    service.block(
        operation_id="op-budget-impl",
        request_hash=ensured.request_hash,
        workflow_run_id="run-budget-1",
        task_id="TASK-BUDGET",
        reason="workflow_budget_exceeded:tokens",
        causation_id=exceeded.id,
        correlation_id="run-budget-1",
    )

    projection = build_workflow_resume_projection(state_dir, _config())

    assert projection["summary"]["operation_pending"] == 0
    assert projection["operation_checkpoints"] == []


@pytest.mark.parametrize("reason", ["fast_stop", "graceful_stop"])
@pytest.mark.parametrize("started", [False, True])
def test_stopped_reader_resumes_same_request_without_repeating_settled_sibling(
    tmp_path: Path, reason: str, started: bool,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state_dir, event_log=log, event_writer=EventWriter(log),
    )
    kwargs = dict(
        workflow_run_id="run-1", operation_id="reader-1",
        operation_type="fanout_reader_child",
        request={"candidate_head": "a" * 40}, task_id="FLOW-1",
    )
    requested = service.ensure_operation(**kwargs)
    if started:
        service.mark_started(
            operation_id="reader-1", request_hash=requested.request_hash,
            workflow_run_id="run-1", task_id="FLOW-1", dispatch_id="reader-attempt",
        )
    service.interrupt(
        operation_id="reader-1", request_hash=requested.request_hash,
        workflow_run_id="run-1", task_id="FLOW-1", reason=reason,
    )
    sibling = service.ensure_operation(**{**kwargs, "operation_id": "reader-2"})
    service.settle(
        operation_id="reader-2", request_hash=sibling.request_hash,
        workflow_run_id="run-1", task_id="FLOW-1",
        admitted_call_result_ref={"ref": "results/2.json", "sha256": "b" * 64},
    )
    projection = build_workflow_resume_projection(state_dir, _config())
    checkpoint = projection["operation_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == "redrive_interrupted_reader_operation"
    assert len(projection["operation_checkpoints"]) == 1
    applied = apply_workflow_resume(
        state_dir, _config(), checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert applied["applied"] == 1
    replay = service.ensure_operation(**kwargs)
    assert replay.status == "requested"
    assert replay.request_hash == requested.request_hash
    assert reduce_workflow_operations(log.read_all())["reader-2"]["status"] == "settled"
    second = apply_workflow_resume(
        state_dir, _config(), checkpoint_id=checkpoint["checkpoint_id"],
    )
    assert second["applied"] == 0
    assert sum(e.type == "workflow.operation.redrive_admitted" for e in log.read_all()) == 1
