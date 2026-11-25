from tests.test_candidate_host_consumption import evidence  # noqa: F401 -- shared freeze fixture

from datetime import datetime, timedelta, timezone

from tests.test_orchestrator_agent_operations import RecordingTransport
from tests.test_orchestrator_agent_semantic_failure import _runtime, _submit_rework
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_obligation_followup import (
    OA_REQUEST, REASSESS, REQUESTED, REVIEW, UNAVAILABLE, pending_reader_obligations,
)
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_operations import prepared_operation_from_checkpoint_event
from zf.runtime.orchestrator_agent_transport import dispatch_orchestrator_agent_operation
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _marker(runtime, flow_kind="prd"):
    prior = write_immutable_json_sidecar(runtime.state_dir, {"decision": "return_to_plan"},
        root="orchestrator-agent/decisions", schema_version="orchestration-decision.v1",
        kind="orchestration_decision", created_by="kernel")
    rows = [{"finding_id": f"{op}-{index}", "operation_id": op,
             "disposition": {"classification": "contract_question" if index == 2 else "execution_unknown"}}
            for op in ("reader-a", "reader-b") for index in range(3)]
    return runtime.event_writer.append(ZfEvent(type="orchestrator.replan_requested", origin="kernel", actor="zf-cli",
        task_id="TASK-1", correlation_id="run-semantic-1", payload={
            "workflow_run_id": "run-semantic-1", "task_map_generation": "g1", "pdd_id": "TASK-1",
            "flow_kind": flow_kind,
            "source_commit": "frozen-H", "candidate_event_id": "evt-frozen-H", "candidate_failure_id": "evt-failed",
            "classification":"oa_semantic_return_to_plan", "orchestrator_operation_id": "old-oa-op",
            "orchestrator_decision_ref": prior, "rework_summary": {"candidate_obligations": rows}}))


def _tick(runtime):
    return run_manager_tick(state_dir=runtime.state_dir, event_log=runtime.event_log, writer=runtime.event_writer,
                            config=runtime.config, project_root=runtime.project_root, spawn_repairs=False,
                            action_filter={REASSESS, REVIEW})


def test_rm_routes_two_distinct_readers_and_delivers_both_failures_to_new_oa_session(tmp_path):
    runtime = _runtime(tmp_path)
    marker = _marker(runtime)
    projection = build_run_manager_projection(runtime.state_dir, config=runtime.config, project_root=tmp_path)
    actions = [a for a in projection["pending_actions"] if a["action"] == REASSESS]
    assert sorted(a["reader_source_operation_id"] for a in actions) == ["reader-a", "reader-b"]
    assert all(len(a["reader_finding_ids"]) == 2 for a in actions)
    assert len({a["checkpoint_id"] for a in actions}) == len({a["recovery_case_id"] for a in actions}) == 2
    for _ in range(5):
        _tick(runtime)
    events = runtime.event_log.read_all()
    unavailable = [e for e in events if e.type == UNAVAILABLE]
    assert len(unavailable) == 2
    assert {e.payload["source_operation_id"] for e in unavailable} == {"reader-a", "reader-b"}
    assert not [e for e in events if e.type == REQUESTED]
    [request] = [e for e in events if e.type == OA_REQUEST]
    transport = RecordingTransport()
    reactor = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=tmp_path)
    reaction = reactor._on_orchestrator_semantic_failure_requested(request)
    assert reaction.action == "checkpoint", reaction.reason
    [checkpoint] = [e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.checkpoint.requested"]
    prepared = prepared_operation_from_checkpoint_event(reactor, checkpoint)
    assert prepared.operation_id != "old-oa-op"
    sources = prepared.context.source_manifest["sources"]
    assert {s["source_id"] for s in sources} >= {"reader-obligation-report", "prior-oa-decision"}
    report = hydrate_sidecar_ref(runtime.state_dir, request.payload["reader_report_ref"]).payload
    assert report["orchestrator_decision_ref"] == marker.payload["orchestrator_decision_ref"]
    assert {r["request_event_id"] for r in report["reader_receipts"]} == {e.id for e in unavailable}
    assert len(report["candidate_obligations"]) == 6
    assert all(r["status"] == "needs_owner" for r in report["reader_receipts"])
    dispatch_orchestrator_agent_operation(reactor, reactor._find_role_by_name("orchestrator"), checkpoint)
    assert transport.sent and transport.sent[0][0] == "orchestrator"
    assert "reader-obligation-report" in transport.sent[0][1].read_text()
    assert pending_reader_obligations(runtime.state_dir, runtime.event_log.read_all()) == []


def test_reader_review_cannot_reuse_product_authority(tmp_path):
    runtime = _runtime(tmp_path)
    _marker(runtime)
    for _ in range(5):
        _tick(runtime)
    request = next(e for e in runtime.event_log.read_all() if e.type == OA_REQUEST)
    from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
    prepared = request_semantic_failure_checkpoint(runtime, request)
    result = apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared))
    assert not result["applied"]
    assert not any(e.type in {"orchestrator.semantic.rework.requested", "task.requeued"}
                   for e in runtime.event_log.read_all())


def test_missing_reader_receipt_escalates_without_starting_duplicate(tmp_path):
    runtime = _runtime(tmp_path)
    marker = _marker(runtime)
    for op in ("reader-a", "reader-b"):
        runtime.event_writer.append(ZfEvent(type=REQUESTED, origin="kernel", actor="zf-runtime",
            causation_id=marker.id, payload={
                "workflow_run_id": marker.payload["workflow_run_id"], "marker_event_id": marker.id,
                "candidate_event_id": marker.payload["candidate_event_id"], "source_operation_id": op,
                "finding_ids": [f"{op}-0", f"{op}-1"]}))
    assert [a["action"] for a in pending_reader_obligations(runtime.state_dir, runtime.event_log.read_all())] == [REVIEW]
    _tick(runtime)
    [request] = [e for e in runtime.event_log.read_all() if e.type == OA_REQUEST]
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    [diagnosis] = pending_reader_obligations(runtime.state_dir, runtime.event_log.read_all(), now=later, config=runtime.config)
    assert diagnosis["action"] == "diagnose-attention" and request.id in diagnosis["source_event_ids"]


def test_unresolved_acknowledgment_delivers_durable_owned_diagnosis(tmp_path):
    from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint

    runtime = _runtime(tmp_path)
    marker = _marker(runtime)
    for _ in range(5):
        _tick(runtime)
    request = next(e for e in runtime.event_log.read_all() if e.type == OA_REQUEST)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    submitted = _submit_rework(runtime, prepared, action="continue")
    outcome = apply_orchestrator_agent_decision(runtime, submitted)
    assert outcome["decision"] == "continue"
    [diagnosis] = pending_reader_obligations(runtime.state_dir, runtime.event_log.read_all(), config=runtime.config)
    assert {request.id, marker.id}.issubset(diagnosis["source_event_ids"])
    assert diagnosis["owner_route"] == "run_manager" and diagnosis["action"] == "diagnose-attention"
    for _ in range(2):
        run_manager_tick(state_dir=runtime.state_dir, event_log=runtime.event_log, writer=runtime.event_writer,
                         config=runtime.config, project_root=runtime.project_root, spawn_repairs=False,
                         action_filter={"diagnose-attention"})
    [handoff] = [e for e in runtime.event_log.read_all() if e.type == "run.manager.autoresearch.requested"
                 and e.payload.get("recovery_case_id") == diagnosis["recovery_case_id"]]
    assert handoff.payload["source_event_ids"] == diagnosis["source_event_ids"]
    assert "next_owner" in handoff.payload["expected_output"]


def test_late_bound_receipts_redeliver_one_revision_and_preserve_old_report(tmp_path, monkeypatch):
    from zf.runtime import candidate_reader_reassessment as reader

    runtime = _runtime(tmp_path)
    marker = _marker(runtime)
    for op in ("reader-a", "reader-b"):
        runtime.event_writer.append(ZfEvent(type=REQUESTED, origin="kernel", actor="zf-runtime",
            causation_id=marker.id, payload={
                "workflow_run_id": marker.payload["workflow_run_id"], "marker_event_id": marker.id,
                "candidate_event_id": marker.payload["candidate_event_id"], "source_operation_id": op,
                "role_instance": "dev-a", "finding_ids": [f"{op}-0", f"{op}-1"]}))
    revised = False
    def provider_state(path, events, *, request_event_id):
        request = next(e for e in events if e.id == request_event_id)
        op = request.payload["source_operation_id"]
        if not revised:
            return {"status": "needs_owner", "reason": "missing admitted evidence"}
        ref = write_immutable_json_sidecar(path, {"findings": request.payload["finding_ids"]},
            root="fixtures/reader-results", kind="reader-result", schema_version="fixture.v1", created_by="test")
        return {"status": "completed", "operation_id": "new-" + op, "source_operation_id": op,
                "request_event_id": request_event_id, "control_result_ref": ref,
                "finding_results":[{"finding_id": f, "outcome": "supported"} for f in request.payload["finding_ids"]]}
    monkeypatch.setattr(reader, "reader_reassessment_state", provider_state)
    _tick(runtime)
    [first] = [e for e in runtime.event_log.read_all() if e.type == OA_REQUEST]
    assert first.payload["unresolved"]
    revised = True
    for _ in range(3):
        _tick(runtime)
    requests = [e for e in runtime.event_log.read_all() if e.type == OA_REQUEST]
    assert len(requests) == 2 and requests[0] == first and not requests[1].payload["reader_evidence_unresolved"]
    assert requests[1].payload["unresolved"] and requests[1].payload["authorizes_delivery"] is False
    assert requests[1].payload["reader_receipt_digest"] != first.payload["reader_receipt_digest"]
    from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
    checkpoint = request_semantic_failure_checkpoint(runtime, requests[1])
    required = {s["source_id"] for s in checkpoint.context.read_policy["required_reads"]}
    assert {"reader-receipt-reader-a", "reader-receipt-reader-b", "prior-oa-decision", "reader-obligation-report"} <= required
    assert len([e for e in runtime.event_log.read_all() if e.type == REQUESTED]) == 2
    acknowledgment = apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, checkpoint, action="continue"))
    assert acknowledgment["decision"] == "continue"
    assert requests[1].payload["planner_disposition_required_finding_ids"] == ["reader-a-2", "reader-b-2"]
    assert not pending_reader_obligations(runtime.state_dir, runtime.event_log.read_all(), config=runtime.config)


def test_role_timeout_honors_exact_lease_and_new_heartbeat(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zf.core.state.task_attempts import TaskAttemptStore
    from zf.runtime.candidate_obligation_followup import _delivery_stalled

    now = datetime.now(timezone.utc)
    older = (now - timedelta(hours=2)).isoformat()
    role = SimpleNamespace(instance_id="reader-a", orphan_escalate_seconds=30)
    event = ZfEvent(type=REQUESTED, ts=older)
    op = "supplemental-reader"
    events = [event, ZfEvent(type="workflow.operation.requested", ts=older,
                             payload={"operation_id":op, "active_attempt_id":"active"})]
    lease = {"operation_id":op, "instance_id":role.instance_id, "status":"sent",
             "lease_expires_at": (now + timedelta(minutes=5)).isoformat()}
    monkeypatch.setattr(TaskAttemptStore, "get", lambda _store, attempt: lease if attempt == "active" else None)
    assert not _delivery_stalled(tmp_path, events, event, op, role, now)
    lease["lease_expires_at"] = older
    assert _delivery_stalled(tmp_path, events, event, op, role, now)
    wrong = ZfEvent(type="worker.heartbeat", actor=role.instance_id, ts=now.isoformat(),
                    payload={"operation_id":op, "attempt_id":"other"})
    assert _delivery_stalled(tmp_path, [*events, wrong], event, op, role, now)
    heartbeat = ZfEvent(type="worker.heartbeat", actor=role.instance_id, ts=now.isoformat(),
                        payload={"operation_id":op, "attempt_id":"active"})
    assert not _delivery_stalled(tmp_path, [*events, heartbeat], event, op, role, now)


def test_rm_delivers_frozen_opt_in_to_actual_reader_request(evidence, monkeypatch):
    from tests.test_candidate_reader_reassessment import setup_scope
    from tests.test_orchestrator_agent_semantic_failure import _config
    from zf.core.config.schema import RoleConfig
    from zf.runtime.workflow_operation import reduce_workflow_operations

    runtime, marker, original, _, _, host_receipt = setup_scope(evidence, monkeypatch)
    config = _config()
    config.workflow.orchestration.autonomous_actions = ["reassess_reader_evidence"]
    config.roles.append(RoleConfig(name="verify", instance_id="reader-1", role_kind="reader", backend="mock"))
    runtime.config = config
    _tick(runtime)
    _tick(runtime)
    events = runtime.event_log.read_all()
    [request] = [e for e in events if e.type == REQUESTED]
    assert request.causation_id == marker.id and request.payload["source_operation_id"] == original["operation_id"]
    assert request.payload["finding_ids"] == ["missing-evidence"]
    scope = hydrate_sidecar_ref(runtime.state_dir, request.payload["evidence_scope_ref"]).payload
    assert scope["authorizes_execution"] is False and host_receipt.id in scope["receipt_refs"]
    assert not [e for e in events if e.type in {OA_REQUEST, UNAVAILABLE}]
    assert reduce_workflow_operations(events)[original["operation_id"]] == original


def test_native_refuted_probe_retains_protected_contract_and_unknown_answer_cannot_close(tmp_path):
    from zf.runtime.candidate_obligation_followup import _report_obligations

    # Native pilot recorded a successful command, a refuted execution-unknown finding,
    # and a separate, unadjudicated protected-owner contract question.
    marker = ZfEvent(type="orchestrator.replan_requested", origin="kernel", payload={"rework_summary": {
        "candidate_obligations": [
            {"finding_id":"missing-evidence", "disposition":{"classification":"execution_unknown"}},
            {"finding_id":"protected-owner", "disposition":{"classification":"contract_question"}},
        ]}})
    receipt = {"status":"completed", "finding_ids":["missing-evidence"], "control_result_ref":{"ref":"admitted"},
               "finding_results":[{"finding_id":"missing-evidence", "outcome":"refuted",
                                   "receipt_ids":["evt-9cccca29272e"]}]}
    status = _report_obligations(marker, [receipt])
    assert status == {"reader_evidence_unresolved":False, "reader_unresolved_finding_ids":[],
                      "planner_disposition_required_finding_ids":["protected-owner"],
                      "product_repair_required_finding_ids":[], "unresolved":True, "authorizes_delivery":False}
    receipt["finding_results"][0]["outcome"] = "unknown"
    status = _report_obligations(marker, [receipt])
    assert status["unresolved"] and status["reader_evidence_unresolved"]
    assert status["reader_unresolved_finding_ids"] == ["missing-evidence"]
    assert status["planner_disposition_required_finding_ids"] == ["protected-owner"]

def test_oa_followup_uses_the_original_flow_policy(tmp_path):
    runtime = _runtime(tmp_path)
    _marker(runtime, flow_kind="issue")
    for _ in range(3):
        _tick(runtime)
    report, = [e for e in runtime.event_log.read_all() if e.type == OA_REQUEST]
    assert report.payload["flow_kind"] == "issue"
