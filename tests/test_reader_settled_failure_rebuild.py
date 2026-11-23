"""A formally admitted execution failure remains eligible for exact child repair."""
from copy import deepcopy

import pytest

from tests.test_reader_fanout_runtime import _RecordingTransport
from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from tests.test_run_manager_reader_rebuild import consume
from zf.runtime.reader_rebuild_admission import reader_rebuild_error
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.workflow_operation import reduce_workflow_operations


def failed_reader(tmp_path):
    from zf.core.config.schema import (ZfConfig, ProjectConfig, RoleConfig, WorkflowConfig,
        WorkflowStageConfig, FanoutChildConfig, FanoutAggregateConfig)
    from zf.runtime.orchestrator import Orchestrator
    fixture = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(fixture)
    state, log = fixture.state_dir, fixture.event_log
    config = ZfConfig(project=ProjectConfig(name="test", state_dir=str(state)),
        roles=[RoleConfig(name="verify-1", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(flow_metadata=fixture.config.workflow.flow_metadata,
            stages=[WorkflowStageConfig(id="issue-lanes-verify", trigger="candidate.ready", topology="fanout_reader",
                children=[FanoutChildConfig(role_instance="verify-1", payload={"task_id":"FLOW-ANCHOR",
                    "output_profile_id":"candidate-verify"})],
                aggregate=FanoutAggregateConfig(mode="wait_for_all", child_success_event="verify.child.completed",
                    child_failure_event="verify.child.failed", success_event="test.passed", failure_event="test.failed"))]))
    token = provision_role_submit_credential(state, "verify-1").read_text().strip()
    transport = _RecordingTransport()
    runtime = Orchestrator(state, config, transport, project_root=fixture.project_root)
    runtime.event_writer.emit("run.started", payload={"workflow_run_id":seeded["run_id"]})
    trigger = next(e for e in log.read_all() if e.type == "candidate.ready")
    runtime.run_once(events=[trigger])
    child = next(e for e in log.read_all() if e.type == "fanout.child.dispatched").payload
    op_id = child["payload"]["operation_id"]
    result = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=runtime.event_writer).submit(
        operation_id=op_id, role_instance="verify-1", credential=token, semantic_result={
            "schema_version":"verification-result.v1", "execution_status":"failed", "verdict":"abstained",
            "failure_class":"reader_execution_failure", "summary":"current input could not be read",
            "verification_owner":"candidate_verify", "verification_tier":"integration",
            "evidence_refs":["tool-output:mock-input-failure"], "requirement_results":[],
            "probe_receipts":[], "reused_command_receipt_ids":[], "rework_items":[]})
    assert result.admitted_event_id
    runtime.run_once(events=[])
    failed = next(e for e in reversed(log.read_all()) if e.type == "fanout.child.failed")
    op = reduce_workflow_operations(log.read_all())[op_id]
    assert op["status"] == "settled" and op["semantic_verdict"] == "abstained"
    payload = {"fanout_id":failed.payload["fanout_id"], "fanout_child_id":"verify-1",
        "workflow_run_id":seeded["run_id"], "target_operation_id":op_id,
        "active_attempt_id":op["active_attempt_id"], "source_event_id":failed.id}
    return state, log, transport, runtime, payload, failed, op


def test_native_admitted_execution_failure_rebuilds_fresh_call(tmp_path):
    state, log, transport, runtime, payload, _, old = failed_reader(tmp_path)
    assert reader_rebuild_error(state, log.read_all(), payload) == ""
    before = len(transport.sent)
    runtime.event_writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        "recommended_route":"controlled_action", "action":"child-rebuild",
        "summary":"input harness repaired; retry exact reader", "action_payload":payload})
    consume(runtime)
    intents = [e for e in log.read_all() if e.type == "repair.action.requested"]
    assert len(intents) == 1
    runtime.run_once(events=intents)
    assert len(transport.sent) == before + 1
    latest = next(e for e in reversed(log.read_all()) if e.type == "fanout.child.dispatched")
    assert latest.payload["operation_id"] != old["operation_id"]
    assert latest.payload["payload"]["retry_of_operation_id"] == old["operation_id"]
    assert reduce_workflow_operations(log.read_all())[old["operation_id"]] == old


@pytest.mark.parametrize("fault", ["passed", "rejected", "completed", "product_failure", "ref", "identity", "bytes"])
def test_settled_retry_requires_exact_execution_failure_proof(tmp_path, fault):
    from zf.runtime.reader_rebuild_admission import _settled_execution_failure
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
    state, log, _, _, _, failed, op = failed_reader(tmp_path)
    assert _settled_execution_failure(state, log.read_all(), op, failed)
    op = deepcopy(op)
    if fault in {"passed", "rejected"}: op["semantic_verdict"] = fault
    elif fault == "ref": op["admitted_control_result_ref"] = {"ref":"other", "sha256":"x"}
    elif fault == "bytes": (state/op["admitted_control_result_ref"]["ref"]).write_text("{}")
    else:
        body = hydrate_sidecar_ref(state, op["admitted_control_result_ref"]).payload
        if fault == "completed": body["execution_status"] = "completed"
        elif fault == "product_failure": body["failure_class"] = "product_defect"
        else: body["operation_id"] = "other"
        ref = write_sidecar_json(state, "artifacts/wrong-result.json", body,
            kind="call_control_result", schema_version="verification-result.v1", created_by="test")
        op["admitted_control_result_ref"] = ref
    assert not _settled_execution_failure(state, log.read_all(), op, failed)
