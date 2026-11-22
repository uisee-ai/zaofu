"""Real recovery/apply/read/admission boundaries with scripted OA and Reader outcomes."""
from types import SimpleNamespace

from tests import test_candidate_host_consumption as host
from tests import test_orchestrator_agent_semantic_failure as oa
from zf.core.config.schema import FanoutAggregateConfig, RoleConfig, RuntimeConfig, WorkflowStageConfig
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.plan_artifact_package import build_plan_artifact_package, package_event_payload, write_plan_artifact_package
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_mixed_recovery_new_consumer_reads_and_admits_original_host_evidence(tmp_path, monkeypatch):
    original_seed = host._seed_frozen_candidate
    run = "run-candidate-authority"
    def seed(runtime, **kwargs):
        metadata = runtime.config.workflow.flow_metadata
        runtime.config = oa._config()
        runtime.config.workflow.flow_metadata = {**metadata, "flow_kind": "prd"}
        runtime.config.workflow.orchestration.autonomous_actions = ["rebind"]
        runtime.config.roles.extend(RoleConfig(name="verify", instance_id=r, role_kind="reader", backend="mock")
                                    for r in ("source-reader", "peer-reader", "consumer-submit"))
        runtime.config.workflow.stages = [WorkflowStageConfig(id="issue-lanes-verify", trigger="candidate.ready",
            topology="fanout_reader", attempt_domain="verify", roles=["source-reader", "peer-reader"],
            aggregate=FanoutAggregateConfig(success_event="test.passed", failure_event="test.failed"))]
        contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
        contract["contract_digest"] = stable_json_sha256(contract)
        ports = [oa._port(runtime.state_dir, name) for name in ("goal_claim_set", "task_map", "planning_result")]
        package = build_plan_artifact_package(workflow_run_id=run, flow_kind="prd", producer_stage_id="prd-plan",
            run_contract=write_run_contract_snapshot(runtime.state_dir, contract), plan_revision="r1",
            task_map_generation="generation-1", produced=ports, required_ports=[p["logical_name"] for p in ports])
        ref = write_plan_artifact_package(runtime.state_dir, package)
        runtime.event_writer.default_origin = "kernel"
        runtime.event_writer.emit("plan.artifact_package.admitted", correlation_id=run,
            payload=package_event_payload(package, ref, status="admitted"))
        fields = {"pdd_id": "FLOW-ANCHOR", "plan_artifact_package_id": ref["package_id"],
                  "plan_artifact_package_ref": ref["ref"], "plan_artifact_package_digest": ref["sha256"]}
        seeded = original_seed(runtime, package_fields=fields, **kwargs)
        runtime.event_writer.emit("fanout.started", correlation_id=run, payload={
            "workflow_run_id": run, "fanout_id": "fanout-candidate", "stage_id": "issue-lanes-verify",
            "trigger_event_id": "evt-candidate-ready", "expected_children": [
                {"child_id": role, "role_instance": role} for role in ("source-reader", "peer-reader")]})
        return seeded
    monkeypatch.setattr(host, "_seed_frozen_candidate", seed)
    evidence = host.evidence.__wrapped__(tmp_path, SimpleNamespace(param=""))
    runtime, seeded, source_op, source_payload, receipt_ref, host_completed = evidence
    runtime.config.runtime = RuntimeConfig(host_verification=runtime.config.runtime.host_verification)
    oa.provision_role_submit_credential(runtime.state_dir, "orchestrator")
    oa.provision_role_artifact_read_credential(runtime.state_dir, "orchestrator", role_name="orchestrator", provider="mock")
    runtime.event_writer.emit("run.admission.admitted", payload={"workflow_run_id": run})
    original_receipt = (runtime.state_dir / receipt_ref["ref"]).read_bytes()
    peers = [(source_op, source_payload, "rejected"), (*host.prepare(runtime, seeded, "peer-reader"), "abstained")]
    for prepared, payload, verdict in peers:
        result = {**payload, "schema_version": "verification-result.v1", "operation_id": prepared.operation_id,
            "request_hash": prepared.request_hash, "attempt_id": "attempt-" + payload["role_instance"],
            "verdict": verdict, "execution_status": "completed" if verdict == "rejected" else "failed",
            "requirement_results": [{"acceptance_id": "TASK-1/AC-1", "status": "failed"}], "rework_items": []}
        ref = write_immutable_json_sidecar(runtime.state_dir, result, root="fixtures/failed-reader",
            kind="call_control_result", schema_version="verification-result.v1", created_by="mock-reader")
        body = {**result, "active_attempt_id": result["attempt_id"], "control_result_ref": ref,
                "semantic_verdict": verdict, "operation_type": "fanout_reader_child"}
        for kind in ("workflow.call.result.admitted", "workflow.operation.settled", "fanout.child.failed"):
            runtime.event_writer.emit(kind, task_id="FLOW-ANCHOR", correlation_id=run, payload=body)
    aggregate = runtime.event_writer.emit("fanout.aggregate.completed", correlation_id=run, payload={
        "workflow_run_id": run, "fanout_id": "fanout-candidate", "stage_id": "issue-lanes-verify",
        "status": "failed", "failed_children": ["source-reader", "peer-reader"]})
    failure = runtime.event_writer.emit("test.failed", correlation_id=run, causation_id=aggregate.id,
        payload={"workflow_run_id": run, "pdd_id": "FLOW-ANCHOR", "task_map_generation": "generation-1",
            "fanout_id": "fanout-candidate", "stage_id": "issue-lanes-verify", "failure_kind": "mixed"})
    request = runtime.event_writer.emit("orchestrator.semantic.failure.requested", task_id="FLOW-ANCHOR",
        correlation_id=run, payload={"workflow_run_id": run, "flow_kind": "prd", "recovery_scope": "candidate",
                                    "problem_class": "semantic", "failure_event_ids": [failure.id]})
    prepared = request_semantic_failure_checkpoint(runtime, request)
    bound = aggregate_candidate_binding(runtime.state_dir, runtime.event_log.read_all(),
                                         failure_id=failure.id, run_id=run, task_id="FLOW-ANCHOR")
    recovery = {"classification": "verification_recovery", "contract_changed": False, "max_replays": 1,
        "failure_event_id": failure.id, "candidate_snapshot_event_id": bound["candidate_event_id"],
        "rationale": "Consume the authenticated evidence and evaluate all retained findings independently.",
        "member_reviews": [{**{k: m[k] for k in ("child_id", "operation_id", "control_result_ref")},
            "disposition": "reverify", "rationale": "Re-evaluate this finding with the bound execution receipt."}
            for m in bound["members"]]}
    decision = oa._submit_rework(runtime, prepared, action="rebind",
                                  directive_fields={"candidate_verification_recovery": recovery})
    outcome = apply_orchestrator_agent_decision(runtime, decision)
    assert outcome["applied"], outcome
    _, consumer, submitter, kwargs = host.consumer_result(evidence)
    assert consumer["contract_snapshot_digest"] == source_payload["contract_snapshot_digest"]
    assert consumer["target_snapshot_digest"] == source_payload["target_snapshot_digest"]
    binding = hydrate_sidecar_ref(runtime.state_dir, consumer["candidate_host_evidence_ref"]).payload
    assert binding["receipts"][0]["receipt_ref"] == receipt_ref
    assert binding["receipts"][0]["source_operation_id"] == source_op.operation_id
    assert submitter.validate_plan_candidate(**kwargs)["status"] == "passed"
    assert submitter.submit(**kwargs).admitted_event_id
    assert (runtime.state_dir / receipt_ref["ref"]).read_bytes() == original_receipt
    assert sum(e.type == "runtime.action.attempt.started" and e.payload.get("kind") == "verify"
               for e in runtime.event_log.read_all()) == 1
