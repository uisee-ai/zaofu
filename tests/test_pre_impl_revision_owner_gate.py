"""A revised Plan must earn a new independent review, OA admission and Owner decision."""

from tests.e2e.prd_ui.worker import candidate
from tests.test_orchestrator_agent_pre_impl_revision import _review
from tests.test_orchestrator_agent_run_plan import _decision
from tests.test_reader_fanout_runtime import _RecordingTransport
from tests.test_writer_fanout_runtime import _init_repo
from zf.core.config.schema import RoleConfig, WorkflowStageConfig
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_operations import _prepared_operation_from_stored_operation
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.run_contract import write_run_contract, stable_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _submit_review_operation(runtime, role, decide, *, operation_id=None):
    operations = runtime.event_log.query(type="workflow.operation.requested")
    op, = [e.payload for e in operations if e.payload.get("role_instance") == role
           and (not operation_id or e.payload["operation_id"] == operation_id)]
    req = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
    attempt = req["active_attempt_id"]
    digest = req["source_manifest_digest"]
    manifest = hydrate_sidecar_ref(runtime.state_dir, {
        "ref": f"artifacts/attempts/{attempt}/source-manifests/{digest}.json", "sha256": digest}).payload
    if req.get("checkpoint"):
        for source in manifest["sources"]:
            read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=source["source_id"],
                                  artifact_id=source["artifact_id"], actor=role, role=role, provider="mock")
    for row in req.get("required_reads", []):
        read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=row["source_id"],
                              artifact_id=row["artifact_id"], json_path=row["json_path"], max_chars=0, max_items=0)
    result = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
        event_writer=runtime.event_writer).submit(operation_id=op["operation_id"], role_instance=role,
        credential=provision_role_submit_credential(runtime.state_dir, role).read_text().strip(),
        semantic_result=decide(req, op["operation_id"], manifest))
    return next(e for e in runtime.event_log.read_all() if e.id == result.canonical_event_id)


def pending_planner(tmp_path, monkeypatch):
    # Use a single README obligation for this routing test. The larger fixture's
    # 8x8 domain requirements belong to its own product-acceptance run.
    directive = "Assign the README command AC and journey to the independent readme-check Reader."
    monkeypatch.setattr("tests.test_orchestrator_agent_pre_impl_revision.FEEDBACK", [directive])
    seed, submitted, old_ready, _, previous, package = _review(tmp_path)
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(seed.state_dir, {**contract, "contract_digest": stable_json_sha256(contract)})
    stage = seed.config.workflow.stages[-1]
    stage.aggregate.synth_role = "plan-critic"
    stage.aggregate.child_success_event = "prd.plan.child.completed"
    stage.aggregate.child_failure_event = "prd.plan.child.failed"
    stage.result_semantics = "artifact_production"
    stage.flow_kind = "prd"
    seed.config.roles.append(RoleConfig(name="plan-critic", instance_id="plan-critic", backend="mock", role_kind="reader"))
    seed.config.workflow.stages.append(WorkflowStageConfig(
        id="implementation", trigger="task_map.ready", topology="fanout_writer_scoped", roles=["dev-1"],
        task_map="task_map.json", synthesize_canonical_tasks=True, flow_kind="prd"))
    _init_repo(tmp_path)
    seed.config.roles.append(RoleConfig(name="readme-check", instance_id="readme-check", backend="mock", role_kind="reader"))
    seed.config.workflow.plan_approval_enabled = True
    seed.config.workflow.flow_metadata.update(flow_kind="prd", artifact_package={"mode": "blocking"},
        result_protocol={"mode": "blocking", "semantic_submit_profiles": {"workflow-read": "blocking", "plan-synth": "blocking"}})
    (tmp_path / "zf.yaml").write_text("project:\n  name: revision-test\n  state_dir: state\n")
    for role in ("planner", "plan-critic"):
        provision_role_submit_credential(seed.state_dir, role)
    transport = _RecordingTransport()
    runtime = Orchestrator(seed.state_dir, seed.config, transport)
    runtime.run_once(events=[submitted])
    retry = runtime.event_log.query(type="prd.scan.completed")[-1]
    runtime.run_once(events=[retry])
    assert [item[0] for item in transport.sent if item[0] != "orchestrator"] == ["planner"]
    def revise(req, op, manifest):
        context = next(s for s in manifest["sources"] if s["source_id"] == "plan-rework-context")
        assert hydrate_sidecar_ref(runtime.state_dir, context).payload["required_actions"] == [directive]
        result = candidate(tmp_path, req, op, replan=True)
        product = next(p["body"] for p in result["plan_ports"] if p["logical_name"] == "product_acceptance_spec")
        product["candidate_reader_assignments"] = [{"role_instance": "readme-check", "journey_ids": ["journey-cli"],
            "acceptance": [{"task_id": "TASK-1", "acceptance_id": "AC-1"}]}]
        return result

    planner = _submit_review_operation(runtime, "planner", revise)
    return runtime, planner, old_ready, previous, package, transport


def submit_critic(runtime):
    def review(req, op, manifest):
        # The mock independently checks the Planner's bound content before issuing its review.
        candidates = [hydrate_sidecar_ref(runtime.state_dir, s).payload for s in manifest["sources"]
                      if s["source_id"].startswith("child-result-")]
        assert len(candidates) == 1 and "README" in candidates[0]["plan_md"]
        task, = candidates[0]["task_map"]["tasks"]
        product = next(p["body"] for p in candidates[0]["plan_ports"] if p["logical_name"] == "product_acceptance_spec")
        seat, = product["candidate_reader_assignments"]
        assert seat["role_instance"] == "readme-check"
        assert seat["acceptance"] == [{"task_id": task["task_id"], "acceptance_id": task["acceptance_criteria"][0]["id"]}]
        assert seat["journey_ids"] == [product["user_journeys"][0]["journey_id"]]
        assert task["acceptance_criteria"][0]["id"] == "AC-1"
        assert task["validation"]["commands"][0]["command"] == "python -m unittest discover -s tests -p test_readme.py"
        return {"status": "completed", "execution_status": "completed", "recommendation": "approve",
                "plan_content_profile": "derived_plan_full", "summary": "Verified README goal, AC and command.",
                "report": {"status": "passed", "recommendation": "approve", "findings": [], "summary": "Checked the revised plan and bound command."}}

    return _submit_review_operation(runtime, "plan-critic", review)


def test_revision_reenters_independent_review_and_holds_business_until_owner(tmp_path, monkeypatch):
    runtime, planner, old_ready, previous, _, transport = pending_planner(tmp_path, monkeypatch)
    runtime.run_once(events=[planner])
    assert [item[0] for item in transport.sent if item[0] != "orchestrator"] == ["planner", "plan-critic"]
    assert runtime.event_log.query(type="task_map.ready")[-1].id == old_ready.id
    assert not runtime.event_log.query(type="plan.approval.requested")

    reviewed = submit_critic(runtime)
    runtime.run_once(events=[reviewed])
    ready = runtime.event_log.query(type="task_map.ready")[-1]
    assert ready.id != old_ready.id and ready.payload["plan_artifact_package_digest"] != previous["sha256"]
    runtime.run_once(events=[ready])
    assert not runtime.event_log.query(type="plan.approval.requested")
    requested = runtime.event_log.query(type="orchestrator.semantic.checkpoint.requested")[-1]
    operation_id = requested.payload["operation_id"]
    operation = load_workflow_operation(runtime.event_log, operation_id)
    assert operation["status"] == "running"
    prepared = _prepared_operation_from_stored_operation(runtime, operation_id=operation_id, operation=operation)
    assert prepared.context.input_body["identity"]["plan_artifact_package_digest"] == ready.payload["plan_artifact_package_digest"]
    submitted_adopt = _submit_review_operation(runtime, "orchestrator", lambda *_: _decision(prepared),
                                               operation_id=operation_id)
    runtime.run_once(events=[submitted_adopt])
    admitted, = [e for e in runtime.event_log.query(type="orchestrator.run_plan.admitted")
                 if e.payload["plan_artifact_package_digest"] == ready.payload["plan_artifact_package_digest"]]
    for _ in range(2):
        runtime.run_once(events=[admitted])
    owner, = runtime.event_log.query(type="plan.approval.requested")
    assert owner.payload["plan_id"] == ready.id
    assert not runtime.event_log.query(type="plan.approved")
    assert not runtime.event_log.query(type="task.pipeline.stage.dispatched")
    assert not any(role == "dev-1" for role, *_ in transport.sent)
    assert not any(e.payload.get("stage_id") == "implementation" for e in runtime.event_log.query(type="fanout.started"))
    assert "TASK-1" not in {task.id for task in runtime.task_store.list_all()}
