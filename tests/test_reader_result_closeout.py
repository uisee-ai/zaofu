"""Protocol correction retains validated inputs and the original operation."""
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.attempt_read_progress import required_read_progress
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_reader_fanout_runtime import _durable_reader_state, _durable_verification_payload


def test_invalid_report_is_corrected_without_restarting_business_or_reading(tmp_path):
    state, log, transport, runtime, _, child = _durable_reader_state(tmp_path)
    p = child["payload"]
    manifest = hydrate_sidecar_ref(state, p["attempt_source_manifest"]).payload
    read_attempt_artifact(state, manifest=manifest, source_id="context",
                          artifact_id="context", json_path="$.facts")
    before = required_read_progress(state, log.read_all(),
                                    attempt_id=p["attempt_id"], operation_id=p["operation_id"])
    payload = _durable_verification_payload(child, state_dir=state, verdict="passed")
    payload["verification_result"]["verdict"] = "invalid-verdict"
    invalid = runtime.event_writer.append(ZfEvent(
        type="verify.child.completed", actor="verify-1", task_id="T-VERIFY",
        correlation_id="run-durable", payload=payload))
    runtime.run_once(events=[invalid])
    assert any(e.type == "workflow.call.result.repair.dispatched" for e in log.read_all())
    assert reduce_workflow_operations(log.read_all())[p["operation_id"]]["status"] == "running"
    corrected = runtime.event_writer.append(ZfEvent(
        type="verify.child.completed", actor="verify-1", task_id="T-VERIFY",
        correlation_id="run-durable",
        payload=_durable_verification_payload(child, state_dir=state, verdict="passed")))
    runtime.run_once(events=[corrected])
    operation = reduce_workflow_operations(log.read_all())[p["operation_id"]]
    assert operation["status"] == "settled"
    assert operation["active_attempt_id"] == p["attempt_id"]
    assert required_read_progress(state, log.read_all(),
                                  attempt_id=p["attempt_id"], operation_id=p["operation_id"]) == before
    assert not any(e.type in {"task.rework.requested", "fanout.child.dispatch_lost"} for e in log.read_all())
