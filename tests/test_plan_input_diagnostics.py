from copy import deepcopy

import pytest

from tests.test_plan_candidate_preflight import _source_index, _task_map
from zf.cli.result import _validation_summary
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.task_map_required_ports import plan_input_declaration_errors

MATRICES = ["execution_spec", "acceptance_matrix", "test_matrix", "real_e2e_matrix"]
SELECTION = {"schema_version": "task-plan-input-selection.v1",
             "implementation": ["execution_spec"], "task-verify": ["execution_spec"]}


def test_compound_selection_names_independent_faults_and_preserves_other_stage():
    policy = {**SELECTION, "implementation": ["foreign", "foreign"], "task-verify": "execution_spec"}
    task = {"task_id": "T1", "evidence_contract": {"plan_input_selection": policy}}
    message = "\n".join(plan_input_declaration_errors({"tasks": [task]}))
    for detail in ["duplicate ports: foreign", "unbound ports: foreign", "task_map.required_plan_ports",
                   "task_execution_input", "task-verify", '["execution_spec"]', *MATRICES]:
        assert detail in message


def test_pinned_profile_and_producers_do_not_implicitly_declare_a_task_selection(tmp_path):
    task_map = _task_map()
    task_map["tasks"][0]["evidence_contract"] = {"plan_input_selection": deepcopy(SELECTION)}
    source_index = _source_index()
    source_index["tasks"][0].pop("source_excerpt")
    candidate = {"task_map": task_map, "source_index": source_index, "plan_md": "# Plan"}
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path/"state", project_root=tmp_path, reports=[{"report": candidate}],
        manifest={"trigger_payload": {"flow_kind": "prd"}},
        metadata={"artifact_package": {"required_ports": MATRICES, "task_input_policy": "task-scoped-v1"}},
    )
    errors = result["errors"]
    assert sum(e["field"] == "task_map.required_plan_ports" for e in errors) == 1
    assert any(e["field"] == "task_map.tasks[0].evidence_contract.plan_input_selection.implementation" and
               "TASK-1" in e["message"] and "execution_spec" in e["message"] for e in errors)
    assert not any(e["code"] == "source_index_invalid" and e["message"].startswith("task_map: ") for e in errors)
    assert any(e["code"] == "source_index_invalid" and "source_excerpt" in e["message"] for e in errors)
    assert result["plan_input_contract"]["flow_required_ports"] == MATRICES
    assert result["plan_input_contract"]["task_map_required_plan_ports"] == []


def test_compact_summary_retains_independent_reader_and_binding_errors():
    rows = [{"code": "task_plan_inputs_undeclared", "field": f"task_map.tasks[{i}].selection",
             "message": f"selection {i}"} for i in range(16)]
    reader_error = {"code": "product_acceptance_spec_invalid", "field": "plan_ports.spec.readers[0].plan_reads",
                    "message": 'Expected {"task_map":["$.tasks[0]"]}'}
    review = _validation_summary({"status": "failed", "errors": rows + [reader_error]})
    assert reader_error in review["errors"] and rows[0] in review["errors"]
    assert len(review["errors"]) == 12 and review["error_count"] == 17


@pytest.mark.parametrize(("flow", "required_origin"), [("prd", "requirement_spec"), ("issue", "issue_spec")])
def test_unspecified_flow_ports_report_real_package_defaults(tmp_path, flow, required_origin):
    from zf.runtime.plan_artifact_package import required_plan_ports

    task_map = _task_map()
    task_map["tasks"][0]["evidence_contract"] = {"plan_input_selection": SELECTION}
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / "state", project_root=tmp_path,
        reports=[{"report": {"task_map": task_map, "source_index": _source_index(), "plan_md": "# Plan"}}],
        manifest={"trigger_payload": {"flow_kind": flow}}, metadata={},
    )
    assert result["plan_input_contract"]["flow_required_ports"] == required_plan_ports(flow_kind=flow)
    assert result["plan_input_contract"]["flow_required_ports"][0] == required_origin


def test_canonical_global_alias_matches_the_pinned_package_name():
    from zf.runtime.plan_artifact_package import required_plan_ports

    selection = {**SELECTION, "implementation": ["execution_spec", "requirement_spec"]}
    task = {"task_id": "T1", "evidence_contract": {"plan_input_selection": selection}}
    task_map = {"required_plan_ports": [*MATRICES, "prd_ref"], "tasks": [task]}
    package_required = required_plan_ports(flow_kind="prd", declared=task_map["required_plan_ports"])
    assert "requirement_spec" in package_required and "prd_ref" not in package_required
    assert plan_input_declaration_errors(task_map, available_ports=package_required) == []
    selection["implementation"] = ["execution_spec", "prd_ref"]
    assert any("unbound ports: prd_ref" in row for row in plan_input_declaration_errors(task_map))


def test_published_skill_example_uses_the_scheduler_and_consumer_contracts():
    import json
    from pathlib import Path

    from zf.runtime.task_map import validate_task_map_payload
    from zf.runtime.writer_task_materialization import writer_task_pipeline_entry_mode

    root = Path(__file__).resolve().parents[1]
    body = (root / "skills/zf-plan-task-map-contract/SKILL.md").read_text()
    snippet = body.split("#### Scoped input and verify-only example", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    task_map = json.loads(snippet)
    task = task_map["tasks"][0]
    assert writer_task_pipeline_entry_mode(task, task) == "verify_only"
    checked = validate_task_map_payload(task_map, require_task_verification=True)
    assert checked.passed, checked.errors
    assert task["verification_read_paths"] == ["tests/test_cli.py"]
    assert task["validation"]["commands"][0]["owner"] == task["acceptance_criteria"][0]["verification_owner"]


def test_summary_reserves_room_for_independent_codes_among_many_generic_fields():
    rows = [{"code": "schema_invalid", "field": f"spec.field{i}", "message": "wrong shape"} for i in range(23)]
    other = {"code": "reader_invalid", "field": "spec.readers", "message": "bad port"}
    summary = _validation_summary({"errors": [*rows, other]})
    assert other in summary["errors"] and len(summary["errors"]) == 12
