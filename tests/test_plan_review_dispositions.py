"""Settle review questions on one candidate and carry only confirmed work into replan."""

from copy import deepcopy

import pytest

from tests.test_plan_review_comparisons import case, read_current_review_inputs
from tests.test_stage_failure_replan import _config, _failure
from zf.core.events.model import ZfEvent
from zf.runtime.fanout_report_failure import project_synth_failure_payload
from zf.runtime.reader_replan_feedback import repair_input_refs
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_failure_replan import plan_reader_stage_replan
from zf.runtime.workflow_operation import reduce_workflow_operations


def mixed_case(tmp_path):
    runtime, svc, args, result, legacy = case(tmp_path, mixed=True)
    op = reduce_workflow_operations(runtime.event_log.read_all())[args["operation_id"]]
    req = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
    digest = req["source_manifest_digest"]
    manifest = hydrate_sidecar_ref(runtime.state_dir, {
        "ref": f"artifacts/attempts/{req['active_attempt_id']}/source-manifests/{digest}.json",
        "sha256": digest,
    }).payload
    source = next(r for r in manifest["sources"] if r["source_id"] == "plan-execution-review")
    checks = hydrate_sidecar_ref(runtime.state_dir, source).payload["execution_comparisons"]
    audit = checks["readonly_pipeline_entry"]["facts"]["entries"][0]
    selected = checks["readonly_command_scope"]["facts"]["entries"][0]
    assert (audit["task_id"], audit["first_stage_on_admission"], selected["verification_owner"],
            selected["command_ids"]) == ("Audit", "verify_ready", "candidate_verify", ["inspect"])
    rows = []
    for key, path, value in (
        ("readonly_pipeline_entry", "entries[0].first_stage_on_admission", "verify_ready"),
        ("cross_task_ordering", "tasks[0].blocked_by", []),
        ("readonly_command_scope", "entries[0].command_ids", ["inspect"]),
    ):
        rows.append({
            "comparison": key, "task_ids": checks[key]["task_ids"], "disposition": "no_repair",
            "conclusion": "The owner selector does not delay the initial entry; the tasks have no dependency.",
            "basis": deepcopy(legacy[0]["basis"]),
            "runtime_evidence": {**{k: source[k] for k in ("source_id", "artifact_id", "sha256")},
                                 "json_path": f"$.execution_comparisons.{key}.facts.{path}", "observed_value": value},
        })
    rows.append(legacy[0])
    return runtime, svc, args, result, legacy[1:], rows


def test_same_candidate_mixed_review_requires_bound_counterevidence_then_routes_once(tmp_path):
    runtime, svc, args, result, _, rows = mixed_case(tmp_path)
    result.update(recommendation="reject")
    report = result["report"]
    report.update(status="failed", recommendation="reject", execution_assessments=rows)
    rows[1].update(disposition="repair_required", conclusion="The narrative imposes an extra prerequisite.")
    report["findings"] = [{"message": "The owner's label alone was a suspected timing issue.", "blocking": False}]
    fix = {"comparison": "cross_task_ordering", "task_id": "A", "observed_gap": "Extra ordering in the narrative",
           "required_change": "Remove the global prerequisite", "done_when": "Both independent tasks can start",
           "basis": deepcopy(rows[1]["basis"])}
    report["fix_items"] = [fix]
    read_current_review_inputs(runtime, args["operation_id"])
    before = [e.id for e in runtime.event_log.read_all()]
    for fault in ("missing", "duplicate", "wrong_stage", "wrong_candidate", "wrong_comparison", "out_of_scope", "unbound_fix", "unbacked"):
        bad = deepcopy(result)
        assessments = bad["report"]["execution_assessments"]
        if fault == "missing":
            assessments.pop()
        elif fault == "duplicate":
            assessments.append(assessments[0])
        elif fault == "wrong_stage":
            assessments[0]["runtime_evidence"]["observed_value"] = "candidate.ready"
        elif fault == "wrong_candidate":
            assessments[0]["runtime_evidence"]["sha256"] = "f"*64
        elif fault == "wrong_comparison":
            assessments[0]["runtime_evidence"] = assessments[1]["runtime_evidence"]
        elif fault == "unbound_fix":
            bad["report"]["fix_items"][0].pop("comparison")
        elif fault == "out_of_scope":
            bad["report"]["fix_items"][0]["task_id"] = "foreign"
        else:
            assessments[1]["disposition"] = "no_repair"
        assert svc.validate_plan_candidate(**args, semantic_result=bad)["errors"], fault
        with pytest.raises(ResultSubmitError):
            svc.submit(**args, semantic_result=bad)
    assert before == [e.id for e in runtime.event_log.read_all()]
    assert not svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    svc.submit(**args, semantic_result=result)
    op = reduce_workflow_operations(runtime.event_log.read_all())[args["operation_id"]]
    ref = op["admitted_control_result_ref"]
    verdict = hydrate_sidecar_ref(runtime.state_dir, ref).payload
    assert verdict["verdict"] == "rejected" and verdict["execution_assessments"] == rows
    settled = next(e for e in runtime.event_log.read_all() if e.type == "fanout.synth.completed")
    assert settled.payload["plan_review_disposition_digest"] == rows[0]["runtime_evidence"]["sha256"]
    failure = _failure()
    failure.payload.update(project_synth_failure_payload({}, {
        **settled.payload, **verdict, "control_result_ref": ref,
    }, report, []))
    origin = ZfEvent(type="issue.requested", payload={"issue_ref": "docs/issue"})
    replan, _ = plan_reader_stage_replan(_config(), [origin, failure], failure)
    assert replan and not plan_reader_stage_replan(_config(), [origin, failure, replan], failure)[0]
    refs = repair_input_refs(runtime.state_dir, {"trigger_payload": replan.payload},
                             workflow_run_id=verdict["workflow_run_id"], task_id="planner", source_event_id=replan.id)
    handoff = hydrate_sidecar_ref(runtime.state_dir, refs[0]).payload
    assert handoff["plan_review_dispositions"]["assessments"] == rows
    assert [(r["comparison"], r["basis"]) for r in handoff["rework_feedback"]] == [(fix["comparison"], fix["basis"])]
    wrong_review = deepcopy(replan.payload)
    wrong_review["plan_review_disposition_ref"]["plan_review_disposition_digest"] = "f" * 64
    with pytest.raises(ValueError, match="frozen execution review"):
        repair_input_refs(runtime.state_dir, wrong_review, workflow_run_id=verdict["workflow_run_id"],
                          task_id="planner", source_event_id=replan.id)
    replan.payload["plan_review_disposition_ref"]["reviewed_plan_candidate_digest"] = "f" * 64
    with pytest.raises(ValueError, match="candidate binding"):
        repair_input_refs(runtime.state_dir, replan.payload, workflow_run_id=verdict["workflow_run_id"],
                          task_id="planner", source_event_id=replan.id)


def test_approval_and_unresolved_owner_question_are_distinct(tmp_path):
    runtime, svc, args, result, other, rows = mixed_case(tmp_path)
    read_current_review_inputs(runtime, args["operation_id"])
    report = result["report"]
    report["execution_assessments"] = [*other, *rows]
    assert not svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    rows[1]["disposition"] = "needs_clarification"
    assert svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    result.update(recommendation="reject")
    report.update(status="failed", recommendation="reject", execution_assessments=rows)
    assert svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    report["owner_decision_items"] = [{
        "decision_id": "timing", "question": "Does an additional ordering constraint apply?",
        "comparison": "cross_task_ordering", "blocking": True, "basis": rows[1]["basis"],
    }]
    assert not svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    svc.submit(**args, semantic_result=result)
    op = reduce_workflow_operations(runtime.event_log.read_all())[args["operation_id"]]
    control = hydrate_sidecar_ref(runtime.state_dir, op["admitted_control_result_ref"]).payload
    assert control["verdict"] == "rejected" and control["owner_decision_items"][0]["decision_id"] == "timing"

def test_wrong_projection_request_is_corrected_on_same_candidate_and_only_routes_author_fields(tmp_path):
    runtime, svc, args, result, assessments = case(tmp_path, readonly_required=True)
    read_current_review_inputs(runtime, args["operation_id"])
    row = assessments[0]
    row.update(disposition="repair_required", conclusion="The frozen export requires Verify but would start in Impl.")
    basis = row["basis"]
    basis["obligation"] = "Verify the frozen payroll export before any implementation."
    field = basis["current_evidence"][0]
    field.update(json_path="$.tasks[0].execution_mode", observed_value="document_review")
    fix = {"comparison": row["comparison"], "task_id": "A", "observed_gap": row["conclusion"],
           "required_change": "Set the Task Map execution_mode to verify_only with the frozen verification contract.",
           "done_when": "The computed entry is verify_only and starts at verify_ready.", "basis": basis}
    report = result["report"]
    result.update(recommendation="reject")
    report.update(status="failed", recommendation="reject", execution_assessments=[row], fix_items=[fix])
    wrong = deepcopy(result)
    old = wrong["report"]["execution_assessments"][0]["runtime_evidence"]
    old.update(json_path="$.execution_comparisons.standard_with_empty_write_scope",
               observed_value={"task_ids": ["A"]})
    wrong["report"]["fix_items"][0]["required_change"] = "Regenerate the runtime facts."
    before = [e.id for e in runtime.event_log.read_all()]
    for _ in range(2):
        errors = svc.validate_plan_candidate(**args, semantic_result=wrong)["errors"]
        assert any(e["field"] == "report.execution_assessments[0].runtime_evidence" for e in errors)
        with pytest.raises(ResultSubmitError):
            svc.submit(**args, semantic_result=wrong)
    assert before == [e.id for e in runtime.event_log.read_all()]
    svc.submit(**args, semantic_result=result)
    events = runtime.event_log.read_all()
    op = reduce_workflow_operations(events)[args["operation_id"]]
    ref = op["admitted_control_result_ref"]
    verdict = hydrate_sidecar_ref(runtime.state_dir, ref).payload
    settled = next(e for e in events if e.type == "fanout.synth.completed")
    failure = _failure()
    failure.payload.update(project_synth_failure_payload({}, {**settled.payload, **verdict, "control_result_ref": ref}, report, []))
    origin = ZfEvent(type="issue.requested", payload={"issue_ref": "frozen-export"})
    replan, _ = plan_reader_stage_replan(_config(), [origin, failure], failure)
    assert replan and not plan_reader_stage_replan(_config(), [origin, failure, replan], failure)[0]
    refs = repair_input_refs(runtime.state_dir, {"trigger_payload": replan.payload}, workflow_run_id=verdict["workflow_run_id"],
                             task_id="planner", source_event_id=replan.id)
    handoff = hydrate_sidecar_ref(runtime.state_dir, refs[0]).payload
    action, = handoff["rework_feedback"]
    assert action["task_id"] == "A" and action["required_change"] == fix["required_change"]
    assert action["basis"]["current_evidence"] == [field]
    assert handoff["plan_review_dispositions"]["assessments"] == [row]
