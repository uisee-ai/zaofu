"""Protocol corrections traverse the real TaskAttempt transport boundary."""
import pytest

from zf.core.events import ZfEvent
from zf.runtime.call_result_repair_reconciliation import reconcile_call_result_repairs
from zf.runtime.result_submit import SemanticResultSubmitService, ResultSubmitError, provision_role_submit_credential
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_reader_fanout_runtime import _durable_reader_state


def rejected_reader(tmp_path):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path, semantic_submit=True)
    config.workflow.task_attempt.mode = "enforce"
    credential = provision_role_submit_credential(state, "verify-1", rotate=False).read_text().strip()
    service = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=runtime.event_writer)
    with pytest.raises(ResultSubmitError, match="result was not admitted"):
        service.submit(operation_id=child["payload"]["operation_id"], role_instance="verify-1",
                       credential=credential, semantic_result={"verdict": "passed", "summary": "incomplete"})
    runtime.event_writer.append(ZfEvent(type="codex.hook.stop", actor="verify-1"))
    return state, log, transport, runtime, config, child


def test_reader_protocol_repair_preserves_sent_transport_attempt(tmp_path):
    state, log, transport, runtime, config, child = rejected_reader(tmp_path)
    before = task_attempt_store(runtime).current_rows()
    operation = reduce_workflow_operations(log.read_all())[child["payload"]["operation_id"]]
    # Do not mock _send_transport_task: duplicate-delivery protection is essential.
    assert reconcile_call_result_repairs(runtime) == 1
    assert len(transport.sent) == 2
    assert task_attempt_store(runtime).current_rows() == before
    assert reduce_workflow_operations(log.read_all())[operation["operation_id"]]["active_attempt_id"] == operation["active_attempt_id"]
    assert reconcile_call_result_repairs(runtime) == 0
    from zf.runtime.orchestrator import Orchestrator
    restarted = Orchestrator(state, config, transport)
    assert reconcile_call_result_repairs(restarted) == 0
    assert len(transport.sent) == 2


def test_uncertain_correction_delivery_is_not_blindly_resent(tmp_path):
    state, log, transport, runtime, config, child = rejected_reader(tmp_path)
    sends = []
    def lost(*args, **kwargs):
        sends.append(args)
        raise OSError("unknown delivery")
    transport.send_task = lost
    with pytest.raises(OSError, match="unknown delivery"):
        reconcile_call_result_repairs(runtime)
    assert reconcile_call_result_repairs(runtime) == 0
    assert len(sends) == 1


@pytest.mark.parametrize("guard", ["expired", "dead", "budget", "dispatch"])
def test_repair_cannot_bypass_delivery_authority(tmp_path, guard):
    state, log, transport, runtime, config, child = rejected_reader(tmp_path)
    op_id = child["payload"]["operation_id"]
    if guard == "expired":
        row = task_attempt_store(runtime).current_rows()[0]
        task_attempt_store(runtime).renew_lease(row["attempt_id"], updated_at=row["updated_at"],
                                             lease_expires_at="2000-01-01T00:00:00+00:00")
    elif guard == "dead":
        transport.is_alive = lambda _: False
    elif guard == "budget":
        runtime._budget_exceeded = lambda _: True
    else:
        op = reduce_workflow_operations(log.read_all())[op_id]
        runtime.event_writer.append(ZfEvent(type="workflow.operation.started", payload={
            "operation_id": op_id, "request_hash": op["request_hash"], "dispatch_id": "new-dispatch"}))
    if guard == "dispatch":
        assert reconcile_call_result_repairs(runtime) == 0
    else:
        with pytest.raises(RuntimeError):
            reconcile_call_result_repairs(runtime)
    assert len(transport.sent) == 1


def test_budget_rejection_can_resume_correction_after_budget_recovers(tmp_path):
    state, log, transport, runtime, config, child = rejected_reader(tmp_path)
    runtime._budget_exceeded = lambda _: True
    with pytest.raises(RuntimeError, match="budget exceeded"):
        reconcile_call_result_repairs(runtime)
    runtime._budget_exceeded = lambda _: False
    assert reconcile_call_result_repairs(runtime) == 1
    assert len(transport.sent) == 2
    assert reconcile_call_result_repairs(runtime) == 0
