"""A signed zero-Writer Planner can send its completed diagnosis to an independent Critic."""

from copy import deepcopy

import pytest

from tests.test_plan_candidate_owner_review import candidate
from tests.test_result_submit import _running_plan_candidate_operation
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.plan_candidate_submit_validation import validate_plan_candidate_semantic
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_read_result import normalize_workflow_read_result


def _diagnosis_trigger(runtime):
    def source(body, schema):
        return write_immutable_json_sidecar(
            runtime.state_dir,
            body,
            root="call-results/control",
            kind="call_control_result",
            schema_version=schema,
            created_by="test",
        )

    run = "run-plan-candidate"
    oa = source(
        {
            "schema_version": "orchestration-decision.v1",
            "workflow_run_id": run,
            "decision": "return_to_plan",
            "execution_status": "completed",
            "plan_artifact_package_digest": "a" * 64,
        },
        "orchestration-decision.v1",
    )
    findings = [
        {
            "finding_id": name,
            "control_result_ref": source({"id": name}, "verification-result.v1"),
        }
        for name in ("finding-A", "finding-B")
    ]
    return {
        "replan_classification": "oa_semantic_return_to_plan",
        "resume_scope": "candidate_diagnosis_only",
        "workflow_run_id": run,
        "candidate_event_id": "evt-frozen",
        "source_commit": "b" * 40,
        "plan_artifact_package_digest": "a" * 64,
        "pdd_id": "task-anchor",
        "rework_of": "evt-source",
        "orchestrator_decision_ref": oa,
        "rework_summary": {"candidate_obligations": findings},
        "operator_feedback": ["Current Owner answer; preserve the original basis."],
    }


@pytest.fixture
def active(tmp_path):
    runtime, prepared, service, token = _running_plan_candidate_operation(
        tmp_path,
        result_semantics="artifact_production",
        validation_setup=_diagnosis_trigger,
        extra_payload={
            "target_commit": "b" * 40,
            "plan_artifact_package_digest": "a" * 64,
        },
    )
    event = next(
        e
        for e in runtime.event_log.read_all()
        if e.type == "workflow.operation.requested"
    )
    req = hydrate_sidecar_ref(runtime.state_dir, event.payload["request_ref"]).payload[
        "request"
    ]
    pinned = req["plan_candidate_validation"]["manifest"]["trigger_payload"]
    sem = candidate(runtime.project_root)
    sem.update(
        candidate_kind="diagnosis_review",
        recommendation="abstain",
        owner_decision_items=[],
        summary="The two frozen reader findings can be evaluated under the recorded Owner answer.",
        operator_feedback=pinned["operator_feedback"],
        finding_results=[
            {
                "finding_id": item["finding_id"],
                "outcome": "unknown",
                "explanation": "Needs independent assessment",
                "evidence_refs": [item["control_result_ref"]["ref"]],
            }
            for item in pinned["rework_summary"]["candidate_obligations"]
        ],
    )
    return runtime, prepared, service, token, req, sem


def test_real_submit_yields_only_planner_child_and_preserves_bound_evidence(
    active, monkeypatch
):
    runtime, prepared, service, token, req, sem = active
    monkeypatch.chdir(runtime.project_root)
    kwargs = dict(
        operation_id=prepared.operation_id,
        role_instance="planner",
        credential=token,
        semantic_result=sem,
    )
    check = service.validate_plan_candidate(**kwargs, project_root=runtime.project_root)
    assert check["status"] == "passed" and check["execution_ready"] is False, check
    result = service.submit(**kwargs)
    assert result.canonical_event_type == "issue.triage.child.completed"
    events = runtime.event_log.read_all()
    admitted = next(e for e in events if e.type == "workflow.call.result.admitted")
    control = hydrate_sidecar_ref(
        runtime.state_dir, admitted.payload["control_result_ref"]
    ).payload
    for key in (
        "candidate_kind",
        "finding_results",
        "operator_feedback",
        "plan_artifact_sha256",
        "source_index_sha256",
    ):
        assert control[key] == sem[key]
    assert not any(
        e.type
        in {"task_map.ready", "plan.approved", "candidate.ready", "task.dispatched"}
        for e in events
    )
    from zf.runtime.result_submit import ResultSubmitError

    with pytest.raises(ResultSubmitError) as repeat:
        service.submit(**kwargs)
    assert repeat.value.code == "duplicate_submit"


@pytest.mark.parametrize(
    "fault",
    [
        "unbound",
        "another-profile",
        "no-context",
        "writer",
        "role",
        "target",
        "attempt",
        "operator",
        "oa",
        "failed",
        "question",
        "executable",
        "source-index",
        "draft",
        "missing-finding",
        "extra-finding",
        "source",
        "evidence-sha",
    ],
)
def test_wrong_identity_artifacts_or_scope_do_not_admit(active, fault):
    runtime, _, _, _, req, candidate_body = active
    req, sem = deepcopy(req), deepcopy(candidate_body)
    trigger = req["plan_candidate_validation"]["manifest"]["trigger_payload"]
    profile = "workflow-read"
    if fault == "unbound":
        trigger.pop("orchestrator_decision_ref")
    elif fault == "another-profile":
        profile = "candidate-verify"
    elif fault == "no-context":
        req.pop("plan_candidate_validation")
    elif fault == "writer":
        trigger["downstream_task_ids"] = ["writer"]
    elif fault == "role":
        req["role_instance"] = "other"
    elif fault == "target":
        req["target_commit"] = "c" * 40
    elif fault == "attempt":
        req["active_attempt_id"] = ""
    elif fault == "operator":
        sem["operator_feedback"] = ["superseded choice"]
    elif fault == "oa":
        trigger["orchestrator_decision_ref"]["sha256"] = "e" * 64
    elif fault == "failed":
        sem["execution_status"] = "failed"
    elif fault == "question":
        sem["owner_decision_items"] = [{"blocking": True}]
    elif fault == "executable":
        sem["plan_ports"] = [{"logical_name": "task_map"}]
    elif fault == "source-index":
        sem["source_index_sha256"] = "f" * 64
    elif fault == "draft":
        sem["plan_artifact_sha256"] = ""
    elif fault == "missing-finding":
        sem["finding_results"].pop()
    elif fault == "extra-finding":
        sem["finding_results"][1] = sem["finding_results"][0]
    elif fault == "source":
        sem["finding_results"][0]["evidence_refs"] = sem["finding_results"][1][
            "evidence_refs"
        ]
    elif fault == "evidence-sha":
        trigger["rework_summary"]["candidate_obligations"][0]["control_result_ref"][
            "sha256"
        ] = "0" * 64
    checked = validate_plan_candidate_semantic(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        request=req,
        semantic=sem,
        profile_id=profile,
    )
    assert checked["status"] == "failed" and checked["execution_ready"] is False, (
        checked
    )


def test_canonical_preflight_and_brief_preserve_review_only_boundary(active):
    runtime, _, _, _, req, sem = active
    from zf.runtime.stage_execution_card import _render_plan_candidate_policy

    label = "\n".join(_render_plan_candidate_policy(req["plan_candidate_validation"]))
    assert "required Plan inputs/outputs" not in label
    for required in (
        "zero Writers",
        "candidate_kind=diagnosis_review",
        "finding_results",
        "result validate",
        "result submit",
    ):
        assert required in label
    report, issues = normalize_workflow_read_result(
        ZfEvent(type="issue.triage.child.completed", payload=sem)
    )
    assert not issues
    check = evaluate_plan_candidate_preflight(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        manifest=req["plan_candidate_validation"]["manifest"],
        metadata={},
        reports=[{"report": report}],
    )
    assert check["status"] == "passed" and not check["execution_ready"], check


def test_completed_diagnosis_reaches_independent_dispatch_gate(active, tmp_path):
    from tests.test_plan_candidate_preflight import _PlanSynthHarness
    from zf.runtime.plan_synth_handoff import build_plan_candidate_input_refs

    runtime, _, _, _, req, sem = active
    reports = [{"child_id": req["child_id"], "report": sem}]
    refs, bindings = build_plan_candidate_input_refs(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        reports=reports,
    )
    child = next(ref for ref in refs if ref["source_id"] == "child-result-planner")
    assert bindings[0]["sha256"] == child["sha256"]
    assert (
        hydrate_sidecar_ref(runtime.state_dir, child).payload["finding_results"]
        == sem["finding_results"]
    )
    assert {sem["plan_artifact_sha256"], sem["source_index_sha256"]} <= {
        ref["sha256"] for ref in refs
    }

    gate_root = tmp_path / "dispatch"
    gate_root.mkdir()
    harness = _PlanSynthHarness(gate_root, reports)
    harness.state_dir, harness.project_root = runtime.state_dir, runtime.project_root

    def busy_critic(**kwargs):
        harness.critic_dispatch_attempted = True
        return False

    harness._ensure_fanout_role_dispatchable = busy_critic
    manifest = {
        **req["plan_candidate_validation"]["manifest"],
        "trace_id": req["workflow_run_id"],
        "trigger_event_id": "evt-frozen",
        "aggregate_config": {
            "success_event": "task_map.ready",
            "failure_event": "issue.plan.failed",
            "synth_role": "plan-critic",
        },
        "children": [],
    }
    harness._dispatch_fanout_synth(
        req["fanout_id"], manifest, "wait_for_all", "plan-critic"
    )
    assert harness.critic_dispatch_attempted
    assert harness.finalized is None
    assert not list(harness.event_log.read_all())
