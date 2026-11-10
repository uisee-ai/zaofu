"""Old shadow provider interruptions require the existing RM/WRC proof chain."""

from pathlib import Path

import pytest

from tests.test_task_pipeline_fault_matrix import _running_operation_v2_attempt, _runtime
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.task_attempt_recovery import pending_task_attempt_recovery_actions
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from zf.runtime.task_pipeline_attempt_recovery import suspend_retryable_task_pipeline_provider_stop
from zf.runtime.task_pipeline_recovery import reconcile_task_pipeline_redrives


def _legacy_interruption(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.config.workflow.task_attempt.mode = "shadow"
    attempt = _running_operation_v2_attempt(runtime)
    suspend_retryable_task_pipeline_provider_stop(
        runtime, attempt, reason="transport_error", causation_id="provider-stop",
        correlation_id="run-1",
    )
    reconcile_task_attempts(runtime)
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    store.update(attempt["attempt_id"], status="failed",
                 failure_reason="lease_expired_shadow_only", updated_at="2026-08-03T00:03:00+00:00")
    return runtime, store, attempt


@pytest.mark.parametrize("case", [
    "manual_restart", "wrong_attempt", "wrong_operation", "wrong_owner", "missing_completed",
    "unrelated_completed", "wrong_run", "other_active_attempt", "settled_operation",
    "nonretryable", "exhausted", "ordinary_failure", "different_shadow_reason",
])
def test_shadow_provider_stop_never_bypasses_recovery_guards(tmp_path: Path, case: str):
    runtime, store, attempt = _legacy_interruption(tmp_path)
    contexts = {"TASK-A": {"workflow_run_id": "run-1"}}
    payload = {
        "operation_id": attempt["operation_id"], "attempt_id": attempt["attempt_id"],
        "recovery_decision_owner": "run_manager",
    }
    if case == "wrong_attempt":
        payload["attempt_id"] = "another-attempt"
    elif case == "wrong_operation":
        payload["operation_id"] = "another-operation"
    elif case == "wrong_owner":
        payload["recovery_decision_owner"] = "operator"
    elif case == "wrong_run":
        contexts["TASK-A"]["workflow_run_id"] = "another-run"
    elif case == "other_active_attempt":
        # A later started/interrupted placement cannot borrow the old row's request.
        from zf.runtime.workflow_operation import WorkflowOperationService, load_workflow_operation
        service = WorkflowOperationService(state_dir=runtime.state_dir,
            event_log=runtime.event_log, event_writer=runtime.event_writer)
        operation = load_workflow_operation(runtime.event_log, attempt["operation_id"])
        service.mark_started(operation_id=attempt["operation_id"], request_hash=operation["request_hash"],
            workflow_run_id="run-1", task_id="TASK-A", active_attempt_id="another-attempt")
        service.interrupt(operation_id=attempt["operation_id"], request_hash=operation["request_hash"],
            workflow_run_id="run-1", task_id="TASK-A", source_attempt_id="another-attempt", reason="provider_stop")
    elif case == "settled_operation":
        runtime.event_writer.append(ZfEvent(type="workflow.operation.settled", origin="kernel",
            payload={"operation_id": attempt["operation_id"], "workflow_run_id": "run-1"}))
    elif case == "nonretryable":
        store.update(attempt["attempt_id"], status="failed", retryable=False, updated_at="2026-08-03T00:04:00+00:00")
    elif case == "exhausted":
        runtime.config.workflow.task_attempt.max_attempts = 1
    elif case == "ordinary_failure":
        store.update(attempt["attempt_id"], status="failed", failure_class="semantic_result_failed", updated_at="2026-08-03T00:04:00+00:00")
    elif case == "different_shadow_reason":
        store.update(attempt["attempt_id"], status="failed", failure_reason="other_failure", updated_at="2026-08-03T00:04:00+00:00")

    request = runtime.event_writer.append(ZfEvent(type="worker.respawn.requested", task_id="TASK-A",
        actor="zf-cli" if case == "manual_restart" else "run-manager", payload=payload))
    if case != "missing_completed":
        runtime.event_writer.append(ZfEvent(type="worker.respawn.completed", actor="impl-1", task_id="TASK-A",
            causation_id="unrelated-request" if case == "unrelated_completed" else request.id))
    assert reconcile_task_pipeline_redrives(runtime, generation_contexts=contexts) == []
    assert not [e for e in runtime.event_log.read_all() if e.type == "workflow.operation.redrive_admitted"]
    assert store.get(attempt["attempt_id"])["status"] == "failed"
    if case in {"nonretryable", "exhausted", "ordinary_failure", "different_shadow_reason"}:
        actions = pending_task_attempt_recovery_actions(runtime.state_dir / "projections",
            canonical_store_path=store.path, max_retry_attempts=runtime.config.workflow.task_attempt.max_attempts)
        assert actions[0]["action"] == "diagnose-attention"


def test_new_shadow_interruption_at_retry_limit_does_not_respawn(tmp_path: Path):
    runtime, store, attempt = _legacy_interruption(tmp_path)
    store.update(attempt["attempt_id"], status="expired", updated_at="2026-08-03T00:04:00+00:00")
    actions = pending_task_attempt_recovery_actions(runtime.state_dir / "projections",
        canonical_store_path=store.path, max_retry_attempts=1)
    assert actions[0]["action_policy"] == "human_escalate"
    assert actions[0]["policy_decision"]["executable"] is False


@pytest.mark.parametrize("status", ["succeeded", "superseded"])
def test_terminal_provider_stop_metadata_cannot_create_new_recovery(tmp_path: Path, status: str):
    runtime, store, attempt = _legacy_interruption(tmp_path)
    store.update(attempt["attempt_id"], status=status, updated_at="2026-08-03T00:04:00+00:00")
    assert pending_task_attempt_recovery_actions(runtime.state_dir / "projections",
        canonical_store_path=store.path, max_retry_attempts=1) == []
    assert reconcile_task_pipeline_redrives(runtime,
        generation_contexts={"TASK-A": {"workflow_run_id": "run-1"}}) == []
