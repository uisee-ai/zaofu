"""Submission and aggregate settlement for a diagnosis-only Plan Critic."""

from copy import deepcopy

import pytest

from tests.test_plan_candidate_diagnosis_inputs import _diagnosis
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_runtime import mark_call_operation_started, prepare_call_operation
from zf.runtime.fanout_artifact_refs import prepare_fanout_synth_reports
from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin
from zf.runtime.orchestrator_fanout import FanoutCoordinationMixin
from zf.runtime.plan_review_claims import POLICY
from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _case(tmp_path, *, handoff_log=None, result_occurrence=None):
    runtime, trigger, frozen = _diagnosis(tmp_path)
    run = trigger["trace_id"]
    feedback = ["Owner confirmed the existing domain for this candidate"]
    trigger.update(workflow_run_id=run, operator_feedback=feedback)
    findings = trigger["rework_summary"]["candidate_obligations"]
    source = {
        "candidate_kind": "diagnosis_review",
        "execution_status": "completed",
        "workflow_run_id": run,
        "fanout_id": "diagnosis-fanout",
        "target_commit": trigger["source_commit"],
        "request_hash": "a" * 64,
        "operation_id": "planner-op",
        "role_instance": "planner",
        "operator_feedback": feedback,
        "finding_results": [
            {
                "finding_id": f["finding_id"],
                "outcome": "refuted",
                "explanation": "Resolved by the frozen contract",
                "evidence_refs": [f["control_result_ref"]["ref"]],
            }
            for f in findings
        ],
    }
    planner_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        source,
        root="test/planner",
        schema_version="workflow-read-result.v1",
        created_by="test",
        kind="control_result",
    )
    planner = runtime.event_writer.append(
        ZfEvent(
            type="prd.plan.child.completed",
            actor="planner",
            payload={"workflow_run_id": run, "control_result_ref": planner_ref},
        )
    )
    runtime.event_writer.append(
        ZfEvent(
            type="workflow.call.result.admitted",
            origin="kernel",
            actor="zf-cli",
            payload={
                "workflow_run_id": run,
                "source_event_id": planner.id,
                "control_result_ref": planner_ref,
                "operation_id": source["operation_id"],
                "request_hash": source["request_hash"],
            },
        )
    )
    child = {
        "child_id": "planner",
        "role_instance": "planner",
        "result_event_id": planner.id if result_occurrence is None else result_occurrence,
        "report_path": str(runtime.state_dir / planner_ref["ref"]),
        "report": source,
    }
    manifest = {
        "fanout_id": source["fanout_id"],
        "trace_id": run,
        "workflow_run_id": run,
        "stage_id": "prd-plan",
        "children": [child],
        "trigger_event_id": "evt-plan",
        "trigger_payload": trigger,
        "aggregate_config": {"success_event": "task_map.ready", "failure_event": "prd.plan.failed"},
    }
    projected = FanoutEvidenceQueriesMixin._fanout_reports(runtime, manifest)
    prepared_reports = prepare_fanout_synth_reports(
        reports=projected,
        manifest=manifest,
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        config=runtime.config,
        roles=runtime.config.roles,
    )
    call = build_plan_synth_call_payload(
        state_dir=runtime.state_dir,
        event_log=handoff_log(runtime.event_log) if handoff_log else runtime.event_log,
        project_root=runtime.project_root,
        manifest=manifest,
        reports=prepared_reports,
        run_id="critic-attempt",
        role_instance="plan-critic",
    )
    if handoff_log:
        return call
    call.update(plan_review_evidence_policy=POLICY, plan_synth_verdict_only=True)
    token = provision_role_submit_credential(runtime.state_dir, "plan-critic").read_text().strip()
    prepared = prepare_call_operation(
        runtime,
        payload=call,
        operation_type="fanout_synth",
        operation_key="current-diagnosis",
        stage_id=manifest["stage_id"],
        task_id="",
        dispatch_id=call["run_id"],
    )
    mark_call_operation_started(runtime, prepared, task_id="", dispatch_id=call["run_id"])
    requested = next(
        e
        for e in runtime.event_log.read_all()
        if e.type == "workflow.operation.requested" and e.payload["operation_id"] == prepared.operation_id
    )
    req = hydrate_sidecar_ref(runtime.state_dir, requested.payload["request_ref"]).payload["request"]
    scope = req["result_identity"]["diagnosis_review"]
    assert scope["finding_ids"] == [f["finding_id"] for f in findings]
    assert scope["candidate_failure_id"] == trigger["rework_of"]
    sm = req["source_manifest_digest"]
    descriptor = {"ref": f"artifacts/attempts/critic-attempt/source-manifests/{sm}.json", "sha256": sm}
    sources = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    runtime.event_writer.append(
        ZfEvent(
            type="fanout.synth.dispatched",
            actor="zf-cli",
            payload={
                **req["result_identity"],
                "attempt_source_manifest": descriptor,
                "operation_id": prepared.operation_id,
                "request_hash": requested.payload["request_hash"],
                "attempt_id": "critic-attempt",
                "run_id": "critic-attempt",
            },
        )
    )
    lookup = {s["source_id"]: s for s in sources["sources"]}

    def cite(source, quote):
        return {
            **{k: lookup[source][k] for k in ("artifact_id", "source_id", "sha256")},
            "json_path": "$",
            "quote": quote,
        }

    basis = {
        "obligation": "Use the confirmed frozen requirement",
        "requirement_evidence": [cite("plan-rework-context", feedback[0])],
        "current_evidence": [cite("child-result-planner", "Resolved by the frozen contract")],
    }
    rows = [
        {
            "finding_id": f["finding_id"],
            "conclusion": "I accept this diagnosis under the confirmed scope",
            "basis": deepcopy(basis),
        }
        for f in findings
    ]
    for i, row in enumerate(rows):
        row["basis"]["current_evidence"][0]["json_path"] = f"$.finding_results[{i}]"
    result = {
        "status": "completed",
        "recommendation": "approve",
        "summary": "Each original finding is accounted for",
        "report": {
            "status": "passed",
            "recommendation": "approve",
            "summary": "Approve diagnosis only",
            "diagnosis_assessments": rows,
        },
    }
    for required in req["required_reads"]:
        offset = 0
        while True:
            page = read_attempt_artifact(
                runtime.state_dir,
                manifest=sources,
                source_id=required["source_id"],
                artifact_id=required["artifact_id"],
                json_path=required["json_path"],
                page_start=offset,
                page_size=8192,
            )
            offset = page["read"]["page_end"]
            if offset == page["read"]["page_total"]:
                break
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=runtime.event_writer
    )
    return (
        runtime,
        manifest,
        result,
        service,
        {"operation_id": prepared.operation_id, "role_instance": "plan-critic", "credential": token},
    )


@pytest.mark.parametrize(
    "fault",
    ["missing", "duplicate", "wrong_value", "wrong_finding", "missing_authority", "blocking", "writer"],
)
def test_diagnosis_verdict_fails_closed_before_admission(tmp_path, fault):
    runtime, _, result, service, args = _case(tmp_path)
    rows = result["report"]["diagnosis_assessments"]
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "wrong_finding":
        rows[0]["basis"]["current_evidence"][0]["json_path"] = "$.operator_feedback"
        rows[0]["basis"]["current_evidence"][0]["quote"] = "Owner confirmed"
    elif fault == "wrong_value":
        rows[0]["basis"]["current_evidence"][0]["quote"] = "This value is absent"
    elif fault == "missing_authority":
        rows[0]["basis"]["requirement_evidence"] = rows[0]["basis"]["current_evidence"]
    elif fault == "blocking":
        result["report"]["fix_items"] = [{"required_change": "Still blocking", "basis": rows[0]["basis"]}]
    else:
        result["report"]["task_map"] = {"tasks": [{"id": "unauthorized"}]}
    before = len(runtime.event_log.read_all())
    check = service.validate_plan_candidate(**args, semantic_result=result)
    assert check["status"] == "failed", check
    with pytest.raises(ResultSubmitError):
        service.submit(**args, semantic_result=result)
    assert len(runtime.event_log.read_all()) == before


@pytest.mark.parametrize("outcome", ["accept", "reject", "superseded"])
def test_frozen_diagnosis_settles_only_its_aggregate(tmp_path, outcome):
    runtime, manifest, result, service, args = _case(tmp_path)
    if outcome == "reject":
        basis = result["report"].pop("diagnosis_assessments")[0]["basis"]
        result["recommendation"] = "reject"
        result["report"].update(
            status="failed",
            recommendation="reject",
            fix_items=[
                {
                    "observed_gap": "Diagnosis omits a constraint",
                    "required_change": "Reconcile the evidence",
                    "done_when": "Current evidence is accounted for",
                    "basis": basis,
                }
            ],
        )
    check = service.validate_plan_candidate(**args, semantic_result=result)
    assert check["status"] == "passed", check
    submitted = service.submit(**args, semantic_result=result)
    events = runtime.event_log.read_all()
    canonical = next(e for e in events if e.id == submitted.canonical_event_id)
    admitted = next(
        e
        for e in events
        if e.type == "workflow.call.result.admitted" and e.payload.get("source_event_id") == canonical.id
    )
    if outcome == "superseded":
        freeze = next(e for e in events if e.id == manifest["trigger_payload"]["candidate_event_id"])
        runtime.event_writer.append(
            ZfEvent(
                type=freeze.type,
                actor=freeze.actor,
                origin=freeze.origin,
                payload={**freeze.payload, "candidate_head_commit": "1" * 40},
            )
        )
    before = len(runtime.event_log.read_all())
    runtime._fanout_manifest = lambda _: manifest
    runtime._cancel_candidate_superseded_replan_fanout = lambda _: False
    runtime._fanout_identity_stale_reason = lambda _: ("", "")
    runtime._fanout_flow_identity_payload = lambda *a, **k: {}
    runtime._fanout_aggregate_started = lambda _: True
    runtime._contract_failure_payload = lambda artifacts, reason: {**artifacts, "reason": reason}
    runtime._finalize_fanout_synth = lambda e: FanoutCoordinationMixin._finalize_fanout_synth(runtime, e)
    from zf.runtime.plan_synth_runtime import PlanSynthRuntimeMixin

    runtime._consume_durable_fanout_aggregate_result = lambda _: None
    PlanSynthRuntimeMixin._handle_fanout_synth_completed(runtime, canonical)
    published = runtime.event_log.read_all()[before:]
    (aggregate,) = [e for e in published if e.type == "fanout.aggregate.completed"]
    if outcome == "accept":
        assert aggregate.payload["status"] == "completed", aggregate.payload
        assert [e.type for e in published] == ["fanout.aggregate.completed"]
        scope = aggregate.payload["candidate_diagnosis_review"]
        assert scope["critic_admitted_event_id"] == admitted.id
        assert scope["candidate_event_id"] == manifest["trigger_payload"]["candidate_event_id"]
        assert aggregate.payload["success_event"] == ""
        PlanSynthRuntimeMixin._handle_fanout_synth_completed(runtime, canonical)
        assert runtime.event_log.read_all()[before:] == published
    else:
        assert aggregate.payload["status"] == "failed"
        assert any(e.type == "prd.plan.failed" for e in published)
    assert not any(
        e.type in {"task_map.ready", "plan.approved", "candidate.ready", "test.passed", "judge.passed"}
        for e in published
    )

@pytest.mark.parametrize("legacy, allowed", [
    ("", False), ("planner", False), ("oa", False), ("planner", True),
])
def test_critic_uses_runtime_signed_history_for_planner_and_oa(tmp_path, legacy, allowed):
    from zf.core.events import EventLog
    from zf.core.security.signing import EventSigner

    def configured_log(seed):
        path = tmp_path / "archive-import" / "events.jsonl"
        path.parent.mkdir()
        signed = EventLog(path, signer=EventSigner(b"fixture-local-signing-material"), allow_unsigned=allowed)
        pre_signing = EventLog(path)
        for event in seed.read_all():
            is_legacy = (
                legacy == "planner" and event.type == "workflow.call.result.admitted"
                and event.payload.get("operation_id") == "planner-op"
            ) or (legacy == "oa" and event.type == "orchestrator.replan_requested")
            (pre_signing if is_legacy else signed).append(event)
        return signed

    if legacy and not allowed:
        reason = "Missing current Planner admission" if legacy == "planner" else "exact frozen replan marker"
        with pytest.raises(ValueError, match=reason):
            _case(tmp_path, handoff_log=configured_log)
    else:
        payload = _case(tmp_path, handoff_log=configured_log)
        assert payload["diagnosis_review"]["planner_admitted_event_id"]


def test_standalone_diagnosis_uses_project_signing_policy(tmp_path, monkeypatch):
    from tests.test_event_signing_wireup import _write_yaml
    from zf.core.events.factory import EventSigningConfigError
    from zf.runtime.plan_candidate_diagnosis_inputs import frozen_diagnosis_events

    key_var = "ZF_DIAGNOSIS_SIGNING_FIXTURE"
    _write_yaml(tmp_path / "zf.yaml", {"event_signing": {"enabled": True, "secret_env": key_var}})
    monkeypatch.delenv(key_var, raising=False)
    with pytest.raises(EventSigningConfigError):
        frozen_diagnosis_events(tmp_path / "state", tmp_path)


@pytest.mark.parametrize(
    ("occurrence", "reason"),
    [("", "lacks its admitted child result_event_id"), ("evt-other-reader-result", "Missing current Planner admission")],
)
def test_critic_handoff_requires_the_current_child_occurrence(tmp_path, occurrence, reason):
    with pytest.raises(ValueError, match=reason):
        _case(tmp_path, result_occurrence=occurrence)
