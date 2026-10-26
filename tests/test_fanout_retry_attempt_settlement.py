"""A repaired CallResult retains its call identity, not its retry dispatch."""
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from tests.test_scheduler_task_attempt import _runtime, _send


def _case(tmp_path: Path):
    runtime, _, state = _runtime(tmp_path)
    first = _send(runtime, state, dispatch_id="first")
    retry = _send(runtime, state, dispatch_id="retry")
    assert first and retry and first.operation_id == retry.operation_id
    source = runtime.event_writer.append(ZfEvent(
        type="flow.verify.child.completed", origin="worker", task_id="TASK-1",
        payload={"workflow_run_id": "RUN-1", "operation_id": first.operation_id,
                 "attempt_id": "first", "dispatch_id": "first"},
    ))
    admitted = runtime.event_writer.append(ZfEvent(
        type="workflow.operation.settled", origin="kernel", task_id="TASK-1",
        payload={"workflow_run_id": "RUN-1", "operation_id": first.operation_id,
                 "admitted_call_result_ref": {"source_event_id": source.id}},
    ))
    child = ZfEvent(
        type="fanout.child.completed", origin="kernel", task_id="TASK-1",
        causation_id=source.id,
        payload={"workflow_run_id": "RUN-1", "operation_id": first.operation_id,
                 "result_event_id": source.id, "run_id": "retry", "attempt_id": "first",
                 "status": "completed"},
    )
    return runtime, TaskAttemptStore(state / "task_attempts.json"), first, retry, admitted, child


@pytest.mark.parametrize("recover", [False, True])
def test_admitted_retry_settles_correct_attempt_and_preserves_old_history(tmp_path, recover):
    runtime, store, first, retry, admitted, child = _case(tmp_path)
    if recover:
        store.update(retry.attempt_id, status="failed", updated_at=child.ts,
                     failure_class="lease_expired", failure_reason="lease_expired_shadow_only")
    runtime.event_writer.append(child)
    if recover:
        reconcile_task_attempts(runtime)
    else:
        runtime._settle_task_attempt_result(admitted)
    assert store.get(retry.attempt_id)["status"] == "succeeded"
    assert store.get(first.attempt_id)["status"] == "superseded"
    assert reconcile_task_attempts(runtime) == 0
    successes = [e for e in runtime.event_log.read_all() if e.type == "task.attempt.succeeded"]
    assert len(successes) == 1
    assert successes[0].payload["attempt_id"] == retry.attempt_id
    assert successes[0].payload["reconciled_shadow_expiry"] is recover


@pytest.mark.parametrize("field", ["origin", "causation_id", "task_id", "workflow_run_id", "operation_id", "run_id", "result_event_id"])
def test_unrelated_completion_cannot_settle_retry(tmp_path, field):
    runtime, store, _, retry, admitted, child = _case(tmp_path)
    if field in {"origin", "causation_id", "task_id"}:
        setattr(child, field, "unrelated")
    else:
        child.payload[field] = "unrelated"
    runtime.event_writer.append(child)
    runtime._settle_task_attempt_result(admitted)
    assert store.get(retry.attempt_id)["status"] == "sent"


def test_delayed_admission_cannot_close_newer_retry(tmp_path):
    runtime, store, _, retry, admitted, child = _case(tmp_path)
    runtime.event_writer.append(child)
    newer = _send(runtime, runtime.state_dir, dispatch_id="retry-2")
    runtime._settle_task_attempt_result(admitted)
    assert store.get(retry.attempt_id)["status"] == "superseded"
    assert store.get(newer.attempt_id)["status"] == "sent"
