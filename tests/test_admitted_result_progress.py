"""Run Manager consumes actual admission events, not synthetic progress claims."""
from tests.test_call_result_admission import _runtime, _persisted_verification_payload
from zf.core.events import ZfEvent
from zf.runtime.run_manager_progress import task_evidence_signals


def test_admission_publishes_current_execution_identity_for_progress(tmp_path):
    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(workflow_run_id="run-1", operation_id="op-1",
        operation_type="agent", request={"prompt": "verify"}, task_id="T1")
    identity = {"workflow_run_id": "run-1", "operation_id": "op-1",
                "attempt_id": "attempt-1", "dispatch_id": "dispatch-1"}
    dispatch = ZfEvent(type="task.dispatched", task_id="T1", origin="kernel", payload=identity)
    outcome = admission.report_legacy_result(
        ZfEvent(type="verify.child.completed", actor="verify-1", task_id="T1",
                payload=_persisted_verification_payload(tmp_path, identity=identity)),
        mode="blocking", operation={"workflow_run_id": "run-1", "operation_id": "op-1",
                                    "request_hash": ensured.request_hash})
    assert outcome.admitted
    admitted = next(e for e in operations.event_log.read_all() if e.type == "workflow.call.result.admitted")
    signal = task_evidence_signals([dispatch, admitted], ["T1"])["T1"]["accepted_result"]
    assert signal is not None
    assert signal["event_id"] == admitted.id
    assert signal["attempt_id"] == "attempt-1"
    assert admitted.payload["semantic_verdict"] == "rejected"  # admission is not business success
