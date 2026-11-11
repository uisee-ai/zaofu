from __future__ import annotations

import hashlib
import json

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.plan_candidate_submit_validation import validate_plan_candidate_semantic
from zf.runtime.workflow_read_result import normalize_workflow_read_result
from tests.test_plan_candidate_preflight import _PlanSynthHarness, _source_index


def candidate(tmp_path):
    body = {"candidate_kind": "owner_review", "execution_status": "completed",
            "result_semantics": "artifact_production", "recommendation": "abstain",
            "summary": "Requirements conflict; retain the draft for independent review.",
            "findings": [], "owner_decision_items": [{"decision_id": "D1",
                "question": "Which mutually exclusive requirement applies?", "blocking": True}]}
    for name, file, data in (("plan_artifact", "draft.md", b"# Draft\nUnresolved requirements."),
                             ("source_index", "source.json", json.dumps(_source_index()).encode())):
        path = tmp_path / file
        path.write_bytes(data)
        body[f"{name}_ref"] = str(path)
        body[f"{name}_sha256"] = hashlib.sha256(data).hexdigest()
    return body


def test_semantic_block_is_completed_reader_output_and_reviewable_draft(tmp_path):
    body = candidate(tmp_path)
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="prd.plan.child.completed", payload={"report": body}))
    assert not issues
    assert result["execution_status"] == "completed"
    assert result["subject_verdict"] == "abstained"
    assert result["failure_class"] == "none"
    check = validate_plan_candidate_semantic(state_dir=tmp_path / ".zf", project_root=tmp_path,
        request={"plan_candidate_validation": {"manifest": {}}}, semantic=result,
        profile_id="workflow-read")
    assert check["status"] == "passed", check
    assert check["execution_ready"] is False


@pytest.mark.parametrize("fault", ["digest", "missing-draft", "source", "execution", "question", "undeclared"])
def test_incomplete_or_tampered_draft_is_not_admitted(tmp_path, fault):
    body = candidate(tmp_path)
    if fault == "digest":
        (tmp_path / "draft.md").write_text("tampered")
    elif fault == "missing-draft":
        (tmp_path / "draft.md").unlink()
    elif fault == "source":
        body["source_index_sha256"] = "0" * 64
    elif fault == "execution":
        body["execution_status"] = "failed"
    elif fault == "question":
        body["owner_decision_items"] = [{"question": "missing ID"}]
    else:
        del body["candidate_kind"]
    result = evaluate_plan_candidate_preflight(state_dir=tmp_path / ".zf", project_root=tmp_path,
        reports=[{"report": body}], manifest={}, metadata={})
    assert result["status"] == "failed"


def test_draft_reaches_real_synth_dispatch_gate_without_synthetic_failure(tmp_path):
    body = candidate(tmp_path)
    harness = _PlanSynthHarness(tmp_path, [{"child_id": "planner", "report": body}])
    def dispatch_gate(**kwargs):
        harness.critic_dispatch_attempted = True
        return False  # Busy critic; production caller must wait, not fabricate a verdict.
    harness._ensure_fanout_role_dispatchable = dispatch_gate
    harness._dispatch_fanout_synth("fanout-plan", {
        "fanout_id": "fanout-plan", "trace_id": "run-plan", "stage_id": "issue-triage",
        "trigger_event_id": "evt-input", "trigger_payload": {"flow_kind": "issue"},
        "aggregate_config": {"success_event": "task_map.ready", "failure_event": "issue.plan.failed",
                             "synth_role": "plan-critic"}, "children": [],
    }, "wait_for_all", "plan-critic")
    assert harness.critic_dispatch_attempted
    assert harness.finalized is None
    assert not list(harness.event_log.read_all())


def test_draft_submit_is_bound_idempotent_and_never_emits_task_map_ready(tmp_path):
    from tests.test_result_submit import _running_plan_candidate_operation
    runtime, prepared, service, token = _running_plan_candidate_operation(
        tmp_path, result_semantics="artifact_production")
    body = candidate(runtime.project_root)
    kwargs = dict(operation_id=prepared.operation_id, semantic_result=body,
                  role_instance="planner", credential=token)
    checked = service.validate_plan_candidate(**kwargs, project_root=runtime.project_root)
    assert checked["status"] == "passed", checked
    submitted = service.submit(**kwargs)
    assert submitted.canonical_event_type == "issue.triage.child.completed"
    from zf.runtime.result_submit import ResultSubmitError
    with pytest.raises(ResultSubmitError) as duplicate:
        service.submit(**kwargs)
    assert duplicate.value.code == "duplicate_submit"
    events = list(runtime.event_log.read_all())
    assert not any(event.type in {"task_map.ready", "plan.approved"} for event in events)
    assert sum(event.type == "issue.triage.child.completed" for event in events) == 1


def test_critic_receives_bound_draft_and_full_package_gate_remains_closed(tmp_path):
    from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, PlanArtifactPackageError
    state = tmp_path / ".zf"
    state.mkdir()
    body = candidate(tmp_path)
    payload = build_plan_synth_call_payload(state_dir=state, project_root=tmp_path,
        manifest={"fanout_id": "f1", "trace_id": "r1", "stage_id": "prd-plan",
                  "trigger_event_id": "e1"}, reports=[{"child_id": "planner", "report": body}],
        run_id="synth-attempt", role_instance="plan-critic")
    child = next(ref for ref in payload["input_refs"] if ref["source_id"] == "child-result-planner")
    assert hydrate_sidecar_ref(state, child).payload == body
    assert any(ref["sha256"] == body["plan_artifact_sha256"] for ref in payload["input_refs"])
    with pytest.raises(PlanArtifactPackageError, match="task_map"):
        build_plan_artifact_package(workflow_run_id="r1", flow_kind="prd", producer_stage_id="prd-plan",
            run_contract={}, plan_revision="r1", task_map_generation="g1", produced=[],
            required_ports=["task_map", "planning_result"])
