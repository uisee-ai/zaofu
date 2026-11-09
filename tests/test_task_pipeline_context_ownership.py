"""Pipeline context rotation cannot become a second task completion owner."""

import pytest

from tests.test_runtime_self_recovery_closures import _orchestrator, _state_dir
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.runtime.long_horizon import apply_completion_audit
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def setup_pipeline(tmp_path, stage="impl"):
    runtime = _orchestrator(_state_dir(tmp_path))
    runtime.task_store.add(Task(id="T", title="unfinished task", status="in_progress",
                               assigned_to="dev", active_dispatch_id="dispatch-1"))
    service = WorkflowOperationService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    operation = service.ensure_operation(workflow_run_id="RUN", operation_id="OP",
        operation_type="task-stage", task_id="T", request={
            "task_pipeline_stage": stage, "operation_generation": 1,
            "task_map_generation": "MAP"})
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    attempt = store.ensure_for_dispatch(run_id="RUN", task_id="T", dispatch_id="dispatch-1",
        role="dev", instance_id="dev", operation_id="OP", briefing_ref="briefings/current.md",
        created_at="2026-11-07T03:00:00Z", lease_expires_at="", max_attempts=3,
        identity_version="operation-v2", placement_epoch=1).attempt
    service.mark_started(operation_id="OP", request_hash=operation.request_hash,
        workflow_run_id="RUN", task_id="T", dispatch_id="dispatch-1", role_instance="dev",
        active_attempt_id=attempt["attempt_id"], lease_id=attempt["lease_id"])
    trigger = runtime.event_writer.append(ZfEvent(type="worker.context.critical", actor="dev",
        task_id="T", correlation_id="RUN", payload={"task_id": "T", "dispatch_id": "dispatch-1",
            "source": "session_reader", "reason": "hard_cap_exceeded"}))
    return runtime, trigger, store


@pytest.mark.parametrize("stage", ["impl", "verify"])
def test_pipeline_context_critical_preserves_operation_and_attempt(tmp_path, stage):
    runtime, trigger, store = setup_pipeline(tmp_path, stage)
    original = store.current_rows()
    decision = runtime._on_context_critical(trigger)
    assert decision.action == "skip"
    assert "provider lifecycle" in decision.reason
    assert store.current_rows() == original
    assert runtime.task_store.get("T").active_dispatch_id == "dispatch-1"
    assert reduce_workflow_operations(runtime.event_log.read_all())["OP"]["status"] == "running"
    assert not any(e.type in {"task.retry_scheduled", "completion_audit.routed"}
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize("later", ["none", "settled", "replaced"])
def test_preupgrade_pipeline_context_schedule_never_reopens_task(tmp_path, later):
    runtime, trigger, store = setup_pipeline(tmp_path)
    apply_completion_audit(state_dir=runtime.state_dir, task_id="T", event_writer=runtime.event_writer,
        trigger_event=trigger, task_store=runtime.task_store, config=runtime.config,
        project_root=runtime.project_root, mutate=False)
    schedule = next(e for e in runtime.event_log.read_all() if e.type == "task.retry_scheduled")
    if later != "none":
        runtime.event_writer.append(ZfEvent(type="workflow.operation.settled", actor="zf-cli",
            task_id="T", payload={"operation_id": "OP", "workflow_run_id": "RUN"}))
        runtime.task_store.update("T", active_dispatch_id="dispatch-new" if later == "replaced" else "")
    if later == "replaced":
        old = store.current_rows()[0]
        store.update(old["attempt_id"], status="superseded", updated_at=trigger.ts)
        service = WorkflowOperationService(state_dir=runtime.state_dir,
            event_log=runtime.event_log, event_writer=runtime.event_writer)
        operation = service.ensure_operation(workflow_run_id="RUN", operation_id="OP-NEW",
            operation_type="task-stage", task_id="T", request={"task_pipeline_stage": "impl",
                "operation_generation": 2, "task_map_generation": "MAP"})
        attempt = store.ensure_for_dispatch(run_id="RUN", task_id="T", dispatch_id="dispatch-new",
            role="dev", instance_id="dev", operation_id="OP-NEW", briefing_ref="briefings/new.md",
            created_at=trigger.ts, lease_expires_at="", max_attempts=3,
            identity_version="operation-v2", placement_epoch=1).attempt
        service.mark_started(operation_id="OP-NEW", request_hash=operation.request_hash,
            workflow_run_id="RUN", task_id="T", dispatch_id="dispatch-new", role_instance="dev",
            active_attempt_id=attempt["attempt_id"], lease_id=attempt["lease_id"])
    before = runtime.task_store.get("T")
    attempts = store.current_rows()
    operations = reduce_workflow_operations(runtime.event_log.read_all())
    for _ in range(2):
        assert runtime._on_completion_scheduled(schedule).action == "skip"
    assert runtime.task_store.get("T") == before
    assert store.current_rows() == attempts
    assert reduce_workflow_operations(runtime.event_log.read_all()) == operations
    assert not any(e.type in {"task.requeued", "workflow.operation.interrupted"}
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize("mismatch", ["actor", "task", "dispatch", "run", "origin", "timeout", "stage", "payload_task"])
def test_unproven_pipeline_context_does_not_suppress_legacy_audit(tmp_path, mismatch):
    runtime, trigger, _ = setup_pipeline(tmp_path, "integration" if mismatch == "stage" else "impl")
    if mismatch == "actor": trigger.actor = "other"
    elif mismatch == "task":
        trigger.task_id = "OTHER"
        runtime.task_store.add(Task(id="OTHER", status="in_progress"))
    elif mismatch == "dispatch": trigger.payload["dispatch_id"] = "other"
    elif mismatch == "run": trigger.correlation_id = "OTHER"
    elif mismatch == "origin": trigger.origin = "worker"
    elif mismatch == "timeout": trigger.type = "agent.timeout"
    elif mismatch == "payload_task": trigger.payload["task_id"] = "OTHER"
    runtime._on_context_critical(trigger)
    assert any(e.type == "completion_audit.routed" for e in runtime.event_log.read_all())


def test_unknown_schedule_source_keeps_legacy_retry(tmp_path):
    runtime, _, _ = setup_pipeline(tmp_path)
    schedule = runtime.event_writer.append(ZfEvent(type="task.retry_scheduled", actor="zf-cli",
        task_id="T", payload={"trigger_event_id": "missing", "dispatch_id": "dispatch-1"}))
    assert runtime._on_completion_scheduled(schedule).action == "move"


def test_cold_runtime_high_context_keeps_lifecycle_recycle_and_current_attempt(tmp_path, monkeypatch):
    from tests.integration.test_context_recycle_e2e import _FakeReader, _FakeTransport, _usage

    runtime, _, store = setup_pipeline(tmp_path)
    runtime.transport = _FakeTransport()
    runtime.event_writer.correlation_id = "RUN"
    runtime._session_readers = {"mock": _FakeReader(_usage(0.95))}
    role = runtime.config.roles[0]
    role.drain_hold_seconds = 180
    clock = [1000.0]
    runtime._now = lambda: clock[0]
    monkeypatch.setattr(runtime, "_ensure_recovery_contract_sufficient", lambda *_: True)
    monkeypatch.setattr(runtime, "_wait_role_ready", lambda *_: True)
    attempts = store.current_rows()
    assert runtime._instance_state == {}

    runtime._check_context_thresholds()
    assert runtime._instance_state["dev"] == "pending_recycle"
    assert runtime.task_store.get("T").status == "in_progress"
    assert runtime.task_store.get("T").active_dispatch_id == "dispatch-1"
    events = runtime.event_log.read_all()
    assert any(e.type == "worker.context.critical" for e in events)
    assert not any(e.type == "completion_audit.routed" for e in events)

    clock[0] += 181
    runtime._check_context_thresholds()
    assert runtime.transport.terminate_calls == ["dev"]
    assert len(runtime.transport.send_task_calls) == 1
    assert runtime.task_store.get("T").active_dispatch_id == "dispatch-1"
    current_attempts = store.current_rows()
    assert len(current_attempts) == 1
    for key in ("attempt_id", "operation_id", "dispatch_id", "lease_id", "placement_epoch"):
        assert current_attempts[0][key] == attempts[0][key]
    assert current_attempts[0]["status"] == "sent"
    events = runtime.event_log.read_all()
    assert reduce_workflow_operations(events)["OP"]["status"] == "running"
    assert any(e.type == "worker.recycled" for e in events)
    assert not any(e.type in {"task.requeued", "workflow.operation.interrupted"} for e in events)
