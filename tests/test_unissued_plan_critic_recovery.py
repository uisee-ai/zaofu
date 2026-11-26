"""Recover only the latest trusted Planner after a pre-delivery Critic WRC failure."""

import pytest

from tests.test_control_actions_recovery import _req
from tests.test_plan_critic_reassessment import _case
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import normalize_call_result_envelope, write_immutable_json_sidecar
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.fanout_artifact_refs import prepare_fanout_synth_reports
from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationService


@pytest.fixture
def case(tmp_path, request):
    prior, runtime, transport, _, _ = _case(tmp_path, stage_id="issue-plan")
    state, log = runtime.state_dir, runtime.event_log
    service = ControlledActionService(state, prior.writer, config=runtime.config, project_root=tmp_path,
                                      actor="operator", source="operator", surface="cli")

    def add(kind, *, cause=None, actor="zf-cli", origin="kernel", **fields):
        return prior.writer.append(ZfEvent(type=kind, origin=origin, actor=actor, correlation_id="run",
            causation_id=cause, task_id="T", payload={"workflow_run_id": "run", **fields}))

    def sidecar(body, schema):
        return write_immutable_json_sidecar(state, body, root="test-inputs", kind="test",
                                            schema_version=schema, created_by="test")

    add("run.admission.admitted", run_id="run", request_id="req")
    first = log.query(type="fanout.started")[0].payload
    trigger = add("plan.requested", reason="new candidate", rework_attempt=2)
    fid, stage = "fanout-new", first["stage_id"]
    add("fanout.started", **{**first, "fanout_id": fid, "trigger_event_id": trigger.id, "trigger_payload": {**first["trigger_payload"], "rework_attempt": 2}})
    candidate = {"operation_id": "new-planner", "request_hash": "2" * 64, "attempt_id": "new-plan-attempt",
                 "child_id": "planner", "role_instance": "planner", "stage_id": stage, "fanout_id": fid,
                 "workflow_run_id": "run", "attempt_domain": "plan", "task_id": "T"}
    report = dict(log.query(type="fanout.child.completed")[0].payload["report"], summary="revised plan, R2")
    report["task_map_ref"] = sidecar(report["task_map"], "task-map.v1")["ref"]
    source = add("prd.plan.child.completed", actor="planner", origin="", **candidate, status="completed")
    control = sidecar({**candidate, **report, "execution_status": "completed", "result_semantics": "artifact_production",
                       "verdict": "passed"}, "workflow-read-result.v1")
    envelope = sidecar(normalize_call_result_envelope(
        source_payload=candidate, control_result=control, workflow_run_id="run", operation_id=candidate["operation_id"],
        request_hash=candidate["request_hash"], source_event_id=source.id, source_event_type=source.type, actor="planner"),
        "call-result-envelope.v1")
    adm = add("workflow.call.result.admitted", **candidate, source_event_id=source.id, envelope_ref=envelope,
              admission_status="admitted", semantic_verdict="passed", control_result_ref=control)
    add("fanout.child.dispatched", **candidate, run_id=candidate["attempt_id"], result_protocol_mode="blocking")
    add("fanout.child.completed", cause=source.id, **candidate, result_event_id=source.id, status="completed",
        report=report, admitted_call_result_ref=envelope, control_result_ref=control)
    projector = FanoutManifestProjector(state)
    m = projector.rebuild(fid, log.read_all())
    reports = prepare_fanout_synth_reports(reports=runtime._fanout_reports(m), manifest=m, state_dir=state,
        project_root=tmp_path, config=runtime.config, roles=runtime.config.roles)
    call = build_plan_synth_call_payload(state_dir=state, project_root=tmp_path, manifest=m,
                                        reports=reports, run_id="bounced-attempt", role_instance="plan-critic")
    req = dict(attempt_domain="plan", role_instance="plan-critic", fanout_id=fid, stage_id=stage,
               result_identity={k:call[k] for k in ("plan_synth_contract_ref", "plan_synth_contract_digest",
                                                      "reviewed_plan_candidate_digest")})
    if getattr(request, "param", "") == "historical_review":
        prior_control = log.query(type="workflow.call.result.admitted")[0].payload["control_result_ref"]
        history = {**prior_control, "source_id": "plan-rejected-review", "allowed_paths": ["$"],
                   "artifact_id": "old-rejection.json", "readable_roles": ["plan-critic"]}
        contract = hydrate_sidecar_ref(state, {"ref": call["plan_synth_contract_ref"],
                                               "sha256": call["plan_synth_contract_digest"]}).payload
        contract["source_bindings"].append({k: history[k] for k in ("source_id", "artifact_id", "ref", "sha256")})
        frozen = sidecar(contract, "plan-synth-contract.v1")
        req["result_identity"].update(plan_synth_contract_ref=frozen["ref"], plan_synth_contract_digest=frozen["sha256"],
                                      reviewed_plan_candidate_digest=frozen["sha256"])
        from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest
        inputs = build_attempt_source_manifest(workflow_run_id="run", task_id="T", attempt_id="bounced-attempt",
            dispatch_id="bounced-attempt", sources=[*call["input_refs"], history])
        req["source_manifest_digest"] = write_attempt_source_manifest(state, inputs)["sha256"]
        req["required_reads"] = [{"source_id": "plan-rejected-review", "json_path": "$"}]
    ops = WorkflowOperationService(state_dir=state, event_log=log, event_writer=prior.writer)
    args = dict(workflow_run_id="run", operation_id="bounced-critic", operation_type="fanout_synth",
                role_instance="plan-critic", active_attempt_id="bounced-attempt", causation_id=trigger.id,
                parent_stage_id=stage, task_id="T")
    ops.ensure_operation(**args, request=req)
    ops.ensure_operation(**args, request={**req, "skills": ["changed-after-registration"]})
    failed = add("fanout.synth.completed", status="failed", stage_id=stage, role_instance="plan-critic",
                 run_id="bounced-attempt", fanout_id=fid)
    add("fanout.aggregate.completed", cause=failed.id, status="failed", synth_event_id=failed.id, fanout_id=fid)
    stage_failed = add("prd.plan.failed", cause=failed.id, stage_id=stage, status="failed", fanout_id=fid)
    escalation = add("human.escalate", actor="orchestrator", origin="", source_event_id=stage_failed.id,
                     failure_class="stage_replan_cap_exhausted", allowed_actions=["start_new_generation"])
    add("run.goal.blocked", cause=escalation.id, status="blocked", escalation_event_id=escalation.id)
    params = dict(resume_planner_handoff_event_id=failed.id, workflow_run_id="run", escalation_event_id=escalation.id,
        planner_admitted_event_id=adm.id, planner_control_sha256=control["sha256"], approval_ref="owner:approved")
    return service, runtime, transport, params, add


def apply(service, params):
    return service.execute(action="stage-replan-new-generation", requested_action="stage-replan-new-generation",
                           requested=_req(params), payload=params)


@pytest.mark.parametrize("verdict", ["reject", "approve"])
def test_owner_restarts_only_unissued_critic_with_new_operation(case, verdict):
    service, runtime, transport, params, _ = case
    result = apply(service, params)
    assert result["ok"], result
    replay = apply(service, params)
    assert replay["replayed"] and replay["review_event_id"] == result["review_event_id"]
    events = runtime.event_log.read_all()
    marker = next(e for e in events if e.id == result["review_event_id"])
    checkpoint = hydrate_sidecar_ref(runtime.state_dir, marker.payload["review_checkpoint_ref"]).payload
    assert checkpoint["planner_control_sha256"] == params["planner_control_sha256"]
    from zf.runtime.orchestrator import Orchestrator
    restarted = Orchestrator(runtime.state_dir, runtime.config, transport)
    restarted._reconcile_reader_fanout_triggers()
    restarted._reconcile_reader_fanout_triggers()
    assert [send[0] for send in transport.sent] == ["plan-critic"]
    (dispatch,) = [e for e in runtime.event_log.query(type="fanout.synth.dispatched")
                   if e.payload.get("fanout_id") == "fanout-new"]
    assert dispatch.payload["operation_id"] != checkpoint["previous_critic_operation_id"]
    assert dispatch.payload["attempt_id"] != "bounced-attempt"
    assert len(runtime.event_log.query(type="workflow.operation.blocked")) == 1
    assert not runtime.event_log.query(type="task_map.ready")

    # A genuine independent rejection routes the same R2 candidate to rework 1/2 in
    # the authorized generation, despite the original producer already being at 2/2.
    from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
    from zf.runtime.artifact_read_ledger import read_attempt_artifact
    manifest = hydrate_sidecar_ref(runtime.state_dir, dispatch.payload["attempt_source_manifest"]).payload
    for r in dispatch.payload["required_reads"]:
        read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=r["source_id"],
                              artifact_id=r["artifact_id"], json_path=r["json_path"])
    source = next(r for r in checkpoint["candidate_inputs"] if r["source_id"] == "child-result-planner")
    citation = {k: source[k] for k in ("source_id", "artifact_id", "sha256")}
    citation.update(json_path="$", quote="revised plan")
    token = provision_role_submit_credential(runtime.state_dir, "plan-critic").read_text().strip()
    submit = SemanticResultSubmitService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    decision = {"status": "completed", "recommendation": "reject",
        "summary": "A current requirement needs correction", "report": {"status": "failed", "recommendation": "reject",
        "summary": "Conflict remains", "fix_items": [{"observed_gap": "Current decision conflicts",
        "required_change": "Reconcile requirement", "done_when": "Conflict removed",
        "basis": {"obligation": "Existing accepted requirement", "current_evidence": [citation],
                  "requirement_evidence": [citation]}}]}}
    if verdict == "approve":
        decision = {"status": "completed", "recommendation": "approve", "summary": "Reviewed R2",
                    "report": {"recommendation": "approve", "status": "passed", "summary": "Reviewed R2"}}
    submitted = submit.submit(operation_id=dispatch.payload["operation_id"], role_instance="plan-critic",
                              credential=token, semantic_result=decision)
    result_event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    restarted.run_once(events=[result_event])
    if verdict == "approve":
        from zf.runtime.plan_admission import emit_upstream_failure_for_bad_task_map
        ready = runtime.event_log.query(type="task_map.ready")[-1]
        assert ready.causation_id == result_event.id
        emit_upstream_failure_for_bad_task_map(restarted, trigger_event=ready, trace_id="run",
            pdd_id="default", reason="Writer admission found an incompatible task port", stage_id="writer",
            plan_admission_incident_id="incident")
    rejected = runtime.event_log.query(type="prd.plan.failed")[-1]
    assert rejected.causation_id == (ready.id if verdict == "approve" else result_event.id)
    assert rejected.payload["stage_replan_generation"] == result["stage_replan_generation"]
    from zf.runtime.stage_failure_replan import plan_reader_stage_replan
    events = runtime.event_log.read_all()
    following = next((e for e in events if e.type == "plan.requested" and e.causation_id == rejected.id), None)
    if not following:
        following, reason = plan_reader_stage_replan(runtime.config, events, rejected, state_dir=runtime.state_dir)
        assert following, reason
    assert following.payload["rework_attempt"] == 1
    assert following.payload["stage_replan_generation"] == result["stage_replan_generation"]
    assert sorted(r["sha256"] for r in following.payload["previous_plan_candidate_refs"]) == sorted(
        r["sha256"] for r in checkpoint["candidate_inputs"])
    if verdict == "approve":
        from dataclasses import replace
        # Immutable pre-upgrade failures lacked the generation. Only the causal, approved
        # ready event can restore it. A changed or unsigned binding cannot spend the cap.
        legacy = replace(rejected, payload={k: v for k, v in rejected.payload.items()
                         if k not in {"stage_replan_generation", "stage_replan_generation_source_event_id"}})
        assert "Writer admission" in following.payload["rework_feedback"][0]["message"]
        for corrupted in (None, "source", "digest", "actor", "generation", "missing_receipt", "verdict"):
            f, history = legacy, list(events)
            if corrupted == "source":
                f = replace(f, causation_id="other-ready")
            if corrupted == "digest":
                f = replace(f, payload={**f.payload, "task_map_digest": "f" * 64})
            if corrupted == "actor":
                f = replace(f, origin="worker")
            if corrupted == "generation":
                f = replace(f, payload={**f.payload, "stage_replan_generation": "old"})
            if corrupted == "missing_receipt":
                history = [e for e in history if not (e.type == "runtime.action.completed" and e.causation_id == marker.id)]
            if corrupted == "verdict":
                history = [e for e in history if e.payload.get("source_event_id") != result_event.id]
            history = [f if e.id == rejected.id else e for e in history]
            retry, reason = plan_reader_stage_replan(runtime.config, history, f, state_dir=runtime.state_dir)
            if corrupted:
                assert retry is None and "unverified_writer_admission_generation" in reason, (corrupted, reason)
            else:
                assert retry.payload["previous_plan_candidate_refs"] == following.payload["previous_plan_candidate_refs"]
                assert retry.payload["rework_attempt"] == 1


@pytest.mark.parametrize("change", ["stale_planner", "digest", "wrong_run", "dispatched", "cancelled",
                                    "late_result", "unapproved", "new_plan", "tampered", "unknown_selector"])
def test_refuses_stale_or_unproved_handoff(case, change):
    service, runtime, transport, params, add = case
    if change == "stale_planner":
        params["planner_admitted_event_id"] = runtime.event_log.query(type="workflow.call.result.admitted")[0].id
    elif change == "digest":
        params["planner_control_sha256"] = "0" * 64
    elif change == "wrong_run":
        params["workflow_run_id"] = "other"
    elif change == "dispatched":
        add("fanout.synth.dispatched", fanout_id="fanout-new", operation_id="bounced-critic")
    elif change == "cancelled":
        add("run.cancelled", status="cancelled")
    elif change == "late_result":
        add("workflow.call.result.admitted", operation_id="bounced-critic")
    elif change == "new_plan":
        add("fanout.started", fanout_id="later-generation", stage_id="issue-plan")
    elif change == "unknown_selector":
        params["resume_planner_handoff_event"] = params.pop("resume_planner_handoff_event_id")
    elif change == "tampered":
        admitted = next(e for e in runtime.event_log.read_all() if e.id == params["planner_admitted_event_id"])
        (runtime.state_dir / admitted.payload["control_result_ref"]["ref"]).write_text("{}")
    else:
        params.pop("approval_ref")
    result = apply(service, params)
    assert not result["ok"] and not transport.sent
    assert not runtime.event_log.query(type="run.goal.updated")


@pytest.mark.parametrize("case", ["historical_review"], indirect=True)
def test_preserves_prior_rejection_role_scope_and_required_reads(case):
    service, runtime, transport, params, _ = case
    result = apply(service, params)
    assert result["ok"], result
    runtime._reconcile_reader_fanout_triggers()
    (dispatch,) = [e for e in runtime.event_log.query(type="fanout.synth.dispatched")
                   if e.payload.get("fanout_id") == "fanout-new"]
    assert [sent[0] for sent in transport.sent] == ["plan-critic"]
    manifest = hydrate_sidecar_ref(runtime.state_dir, dispatch.payload["attempt_source_manifest"]).payload
    (history,) = [r for r in manifest["sources"] if r["source_id"] == "plan-rejected-review"]
    assert history["sha256"] == runtime.event_log.query(type="workflow.call.result.admitted")[0].payload["control_result_ref"]["sha256"]
    assert history["allowed_paths"] == ["$"]
    assert [r["json_path"] for r in dispatch.payload["required_reads"] if r["source_id"] == "plan-rejected-review"] == ["$"]


def test_owner_can_resume_partial_checkpoint_publication_once(case, monkeypatch):
    import zf.runtime.unissued_plan_critic_recovery as recovery
    service, runtime, _, params, _ = case
    with monkeypatch.context() as unavailable:
        def interrupted(**kwargs):
            raise OSError("injected interruption")
        unavailable.setattr(recovery, "reopen_workflow_managed_task_from_run_recovery", interrupted)
        assert not apply(service, params)["ok"]
    assert not apply(service, {**params, "approval_ref": "another authorization"})["ok"]
    assert apply(service, params)["ok"]
    assert len(runtime.event_log.query(type="run.goal.updated")) == 1
    assert sum(e.payload.get("recovery_kind") == recovery.KIND for e in
               runtime.event_log.query(type="fanout.aggregate.started")) == 1
