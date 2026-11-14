from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_operation_resume_checkpoints
from zf.runtime.workflow_resume_apply import _apply_operation_checkpoint
from tests.test_runtime_self_recovery_closures import _state_dir, _orchestrator
from tests.test_task_pipeline_fault_matrix import _running_operation_v2_attempt


def test_completion_retry_interrupts_exact_operation_before_requeue(tmp_path):
    runtime = _orchestrator(_state_dir(tmp_path))
    runtime.task_store.add(Task(id="TASK-A", title="retry", status="in_progress",
                               assigned_to="impl-1", active_dispatch_id="dispatch-1"))
    attempt = _running_operation_v2_attempt(runtime)
    event = runtime.event_writer.append(ZfEvent(
        type="task.retry_scheduled", actor="zf-cli", origin="kernel", task_id="TASK-A",
        payload={"dispatch_id": "dispatch-1", "route": "retry", "reason": "context critical"},
    ))
    runtime._on_completion_scheduled(event)
    runtime._on_completion_scheduled(event)
    events = runtime.event_log.read_all()
    operation = reduce_workflow_operations(events)["op-task-a-impl-g1"]
    assert operation["status"] == "suspended"
    assert operation["reason"] == "completion_audit_retry"
    assert runtime.task_store.get("TASK-A").status == "backlog"
    rows = TaskAttemptStore(runtime.state_dir / "task_attempts.json").rows()
    assert next(r for r in rows if r["attempt_id"] == attempt["attempt_id"])["status"] == "superseded"
    interrupted = [e for e in events if e.type == "workflow.operation.interrupted"]
    assert len(interrupted) == 1
    assert interrupted[0].causation_id == event.id
    assert events.index(interrupted[0]) < next(i for i,e in enumerate(events) if e.type == "task.requeued")
    checkpoints = build_workflow_operation_resume_checkpoints(runtime.state_dir)
    assert len(checkpoints) == 1
    assert checkpoints[0].safe_resume_action == "redrive_interrupted_task_pipeline_operation"
    result = _apply_operation_checkpoint(
        runtime.task_store, runtime.event_writer, checkpoints[0], state_dir=runtime.state_dir,
    )
    assert result.applied
    resumed = reduce_workflow_operations(runtime.event_log.read_all())[operation["operation_id"]]
    assert resumed["status"] == "requested"
    assert resumed["request_hash"] == operation["request_hash"]
    assert resumed["operation_generation"] == operation["operation_generation"]


def test_stale_completion_retry_preserves_current_operation(tmp_path):
    runtime = _orchestrator(_state_dir(tmp_path))
    runtime.task_store.add(Task(id="TASK-A", title="retry", status="in_progress",
                               assigned_to="impl-1", active_dispatch_id="dispatch-1"))
    _running_operation_v2_attempt(runtime)
    runtime._on_completion_scheduled(ZfEvent(
        type="task.retry_scheduled", actor="zf-cli", task_id="TASK-A",
        payload={"dispatch_id": "old-dispatch", "route": "retry"},
    ))
    assert reduce_workflow_operations(runtime.event_log.read_all())["op-task-a-impl-g1"]["status"] == "running"
