"""An exhausted, never-delivered reader request cannot retain a role forever."""
import pytest

from zf.core.events import ZfEvent
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.workflow_operation import reduce_workflow_operations


def _waiting(tmp_path):
    from tests.test_reader_target_pinning import _state

    _, state, log, _, runtime = _state(tmp_path, workdirs=False)
    service = workflow_operation_service(runtime)
    service.ensure_operation(
        workflow_run_id="run", operation_id="old-op", operation_type="fanout_reader_child",
        parent_stage_id="verify", role_instance="reader", active_attempt_id="attempt",
        task_id="T", request={"fanout_id": "old-fanout", "child_id": "reader", "stage_id": "verify"},
    )
    failure = ZfEvent(type="fanout.child.failed", origin="kernel", correlation_id="run", payload={
        "workflow_run_id": "run", "fanout_id": "old-fanout", "child_id": "reader",
        "stage_id": "verify", "role_instance": "reader", "run_id": "attempt",
        "failure_class": "reader_environment_wait_exhausted", "reason": "resource wait expired",
    })
    return state, log, runtime, failure


def test_runtime_reconciles_unstarted_request_after_failed_wait_once(tmp_path):
    _, log, runtime, failure = _waiting(tmp_path)
    log.append(failure)
    runtime._recover_unrecorded_reader_fanout_results()
    assert reduce_workflow_operations(log.read_all())["old-op"]["status"] == "failed"
    runtime._recover_unrecorded_reader_fanout_results()
    terminal = [e for e in log.read_all() if e.type == "workflow.operation.failed"]
    assert len(terminal) == 1
    assert terminal[0].causation_id == failure.id
    assert not any(e.type in {"task.done", "test.passed", "call.result.admitted"} for e in log.read_all())


@pytest.mark.parametrize("change", [
    "workflow_run_id", "fanout_id", "child_id", "stage_id", "role_instance", "run_id",
    "untrusted", "dispatched", "started", "newer_request", "settled", "bad_digest",
])
def test_wait_closure_preserves_unrelated_delivered_or_newer_work(tmp_path, change):
    state, log, runtime, failure = _waiting(tmp_path)
    if change in failure.payload:
        failure.payload[change] = "other"
    if change == "untrusted":
        failure.origin = "worker"
    if change in {"dispatched", "started"}:
        log.append(ZfEvent(
            type="fanout.child.dispatched" if change == "dispatched" else "workflow.operation.started",
            payload={"operation_id": "old-op", "role_instance": "reader", "run_id": "attempt"},
        ))
    log.append(failure)
    if change == "newer_request":
        log.append(ZfEvent(type="workflow.operation.requested", payload={"operation_id": "old-op"}))
    if change == "settled":
        log.append(ZfEvent(type="workflow.operation.settled", payload={"operation_id": "old-op"}))
    if change == "bad_digest":
        op = reduce_workflow_operations(log.read_all())["old-op"]
        (state / op["request_ref"]["ref"]).write_text("{}")
    before = reduce_workflow_operations(log.read_all())["old-op"]["status"]
    runtime._recover_unrecorded_reader_fanout_results()
    assert reduce_workflow_operations(log.read_all())["old-op"]["status"] == before
    assert not any(e.type == "workflow.operation.failed" for e in log.read_all())
