"""A same-freeze Candidate retains seven admitted Reader results across one native recovery."""
from tests.test_candidate_host_consumption import evidence
from tests.test_reader_fanout_runtime import _RecordingTransport
from tests.test_run_manager_resident import _resident_config
from zf.core.config.schema import (
    FanoutAggregateConfig, FanoutChildConfig, ProjectConfig, RoleConfig, RuntimeRecoveryAuthorization,
    WorkflowConfig, WorkflowStageConfig, ZfConfig,
)
from zf.core.events import ZfEvent
from zf.runtime import candidate_host_navigation as navigation, run_manager
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.run_admission import admit_workflow_invoke
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_briefing import verification_result_template
from zf.runtime.workflow_operation import reduce_workflow_operations


def _submit(runtime, child, *, host_receipt, abstain=False):
    request = child.payload["payload"]
    role = child.payload["role_instance"]
    state = runtime.state_dir
    manifest = hydrate_sidecar_ref(state, request["attempt_source_manifest"]).payload
    for ref in request["required_reads"]:
        read_attempt_artifact(state, manifest=manifest, role=role, actor=role,
                              **{k: ref[k] for k in ("source_id", "artifact_id", "json_path")})
    contract = hydrate_sidecar_ref(state, {"ref": request["contract_snapshot_ref"],
                                          "sha256": request["contract_snapshot_digest"]}).payload
    semantic = verification_result_template(contract)
    semantic.update(summary="Independent review of the admitted host receipt", probe_receipts=[],
                    reused_command_receipt_ids=[], host_execution_receipt_ids=[host_receipt],
                    evidence_refs=[f"event:{host_receipt}"])
    for row in semantic["requirement_results"]:
        row["evidence_refs"] = [f"event:{host_receipt}"]
    if abstain:
        semantic.update(verdict="abstained", execution_status="failed", failure_class="verifier_execution_failure",
                        summary="Cannot complete media review", host_execution_receipt_ids=[], requirement_results=[])
    token = provision_role_submit_credential(state, role, rotate=False).read_text().strip()
    service = SemanticResultSubmitService(state_dir=state, event_log=runtime.event_log,
                                           event_writer=runtime.event_writer)
    result = service.submit(operation_id=request["operation_id"], role_instance=role, credential=token,
                            semantic_result=semantic)
    assert result.admitted_event_id
    return result.admitted_event_id


def test_seven_admitted_candidate_readers_survive_replay_of_eighth(evidence, monkeypatch):
    seed, frozen, _, _, _, host_receipt = evidence
    state, roles = seed.state_dir, [f"verify-lane-{i}" for i in range(8)]
    stage = WorkflowStageConfig(id="issue-lanes-verify", trigger="candidate.ready", topology="fanout_reader",
        children=[FanoutChildConfig(role_instance=role, payload={"task_id": "FLOW-ANCHOR",
                  "output_profile_id": "candidate-verify"}) for role in roles],
        aggregate=FanoutAggregateConfig(mode="wait_for_all", child_success_event="verify.child.completed",
            child_failure_event="verify.child.failed", success_event="test.passed", failure_event="test.failed"))
    cfg = ZfConfig(project=ProjectConfig(name="seven-plus-one", state_dir=str(state)),
        roles=[RoleConfig(name=role, backend="mock", role_kind="reader") for role in roles],
        workflow=WorkflowConfig(flow_metadata=seed.config.workflow.flow_metadata, stages=[stage]))
    cfg.runtime.host_verification = seed.config.runtime.host_verification
    cfg.runtime.run_manager = _resident_config(session_mode="dedicated").runtime.run_manager
    cfg.runtime.run_manager.recovery_authorization = RuntimeRecoveryAuthorization("owner:one-reader-rebuild", 1)
    for role in roles:
        provision_role_submit_credential(state, role)
    transport = _RecordingTransport()
    runtime = Orchestrator(state, cfg, transport, project_root=seed.project_root)
    writer, log, run = runtime.event_writer, runtime.event_log, frozen["run_id"]
    admission = admit_workflow_invoke(runtime, ZfEvent(type="workflow.invoke.requested",
                                                       payload={"workflow_run_id": run}))
    assert admission.status == "admitted"
    writer.emit("run.started", payload={"workflow_run_id": run})
    trigger = next(e for e in log.read_all() if e.type == "candidate.ready")
    choose = navigation.host_navigation_entries
    failed_role = roles[-1]
    with monkeypatch.context() as previous:
        def previous_navigation(*args, role_name):
            entries = choose(*args, role_name=role_name)
            return [{**r, "media_sources": []} for r in entries] if role_name == failed_role else entries

        previous.setattr(navigation, "host_navigation_entries", previous_navigation)
        runtime.run_once(events=[trigger])
    initial = {e.payload["role_instance"]: e for e in log.read_all() if e.type == "fanout.child.dispatched"}
    assert set(initial) == set(roles)
    common_contract, = {e.payload["payload"]["contract_snapshot_digest"] for e in initial.values()}
    assert {e.payload["payload"]["target_commit"] for e in initial.values()} == {frozen["candidate_head"]}
    admitted = {role: _submit(runtime, initial[role], host_receipt=host_receipt.id,
                              abstain=role == failed_role) for role in roles}
    # The fanout reconciler advances one settled child per kernel tick.
    for _ in roles:
        runtime.run_once(events=[])
    before = reduce_workflow_operations(log.read_all())
    healthy = {role: initial[role].payload["operation_id"] for role in roles[:-1]}
    assert all(before[op]["status"] == "settled" and before[op]["semantic_verdict"] == "passed"
               for op in healthy.values())
    old_failure = initial[failed_role].payload["operation_id"]
    assert before[old_failure]["semantic_verdict"] == "abstained"
    assert len([e for e in log.read_all() if e.type == "test.failed"]) == 1

    def tick():
        run_manager.run_manager_tick(state_dir=state, writer=writer, config=cfg, project_root=seed.project_root,
                                     event_log=log, spawn_repairs=False)

    briefing = state / "resident.md"
    briefing.write_text("Run Manager")
    writer.emit("run.manager.resident.spawned", payload={"ready": True, "session_mode": "dedicated",
                                                        "tmux_session": "seven-plus-one"})
    writer.emit("run.manager.resident.prompted", payload={"prompted": True, "briefing_path": str(briefing)})
    writer.emit("run.manager.agent.observation", actor="run-manager", payload={"status": "watching"})
    monkeypatch.setattr(run_manager, "_resident_pane_display", lambda *a: {"ok": True, "current_command": "codex"})
    monkeypatch.setattr(run_manager, "_send_tmux_prompt", lambda *a, **kw: {"ok": True})
    tick()
    diagnosis, = [e for e in log.read_all() if e.type == "run.manager.resident.prompted" and e.payload.get("reprompt")]
    context = hydrate_sidecar_ref(state, diagnosis.payload["diagnosis_context"]["recovery_context_ref"]).payload
    card = context["reader_rebuild"]
    assert card["method_change"]["status"] == "improved"
    assert card["recommendation"]["action_payload"]["fanout_child_id"] == failed_role
    writer.emit("run.manager.agent.recommendation", actor="run-manager",
                payload={**card["recommendation"], "request_id": diagnosis.payload["request_id"]})
    tick()
    intent, = [e for e in log.read_all() if e.type == "repair.action.requested"]
    # Restart the kernel on the persisted project, then replay the exact repair twice.
    recovered = Orchestrator(state, cfg, transport, project_root=seed.project_root)
    for _ in range(2):
        recovered.run_once(events=[intent])
    dispatches = [e for e in log.read_all() if e.type == "fanout.child.dispatched"]
    assert len(dispatches) == len(transport.sent) == 9
    replacement = dispatches[-1]
    assert replacement.payload["role_instance"] == failed_role
    new_request = replacement.payload["payload"]
    assert (new_request["target_commit"], new_request["contract_snapshot_digest"]) == (frozen["candidate_head"], common_contract)
    assert new_request["retry_of_operation_id"] == old_failure
    assert set(reduce_workflow_operations(log.read_all())) - set(before) == {new_request["operation_id"]}
    _submit(recovered, replacement, host_receipt=host_receipt.id)
    recovered.run_once(events=[])
    events = log.read_all()
    after = reduce_workflow_operations(events)
    assert after[new_request["operation_id"]]["semantic_verdict"] == "passed"
    assert after[old_failure] == before[old_failure]
    assert [e.payload["status"] for e in events if e.type == "fanout.aggregate.completed"
            and e.payload.get("fanout_id") == replacement.payload["fanout_id"]] == ["failed", "completed"]
    assert len([e for e in events if e.type == "test.passed"]) == 1
    for role, op_id in healthy.items():
        assert after[op_id] == before[op_id]
        assert [e.id for e in events if e.type == "workflow.call.result.admitted"
                and e.payload.get("operation_id") == op_id] == [admitted[role]]
