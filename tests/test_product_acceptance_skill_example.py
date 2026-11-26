"""Keep the shipped Planner example executable against admission, not prose."""

import json
from pathlib import Path

import pytest

from zf.runtime.plan_candidate_preflight import _product_acceptance_spec_errors


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = "zf-plan-task-map-contract/references/product-acceptance.json"


@pytest.mark.parametrize("source", ["skills", ".codex/skills", ".claude/skills"])
def test_shipped_product_acceptance_descriptor_passes_preflight(source):
    descriptor = json.loads((ROOT / source / EXAMPLE).read_text())
    assert descriptor["logical_name"] == "product_acceptance_spec"
    assert descriptor["schema_version"] == "product_acceptance_spec.v1"
    task_map = {"tasks": [
        {"task_id": "TASK-SAVE", "acceptance_criteria": [{"id": "AC-PERSIST"}]},
        {"task_id": "TASK-ASSEMBLY", "acceptance_criteria": [{"id": "AC-DISPLAY"}]},
    ]}
    assert descriptor["body"]["candidate_reader_assignments"][0]["integration_check_ids"] == ["save-and-view"]
    assert _product_acceptance_spec_errors(
        descriptor["body"], task_map=task_map, manifest={}, flow_kind="prd",
    ) == []


@pytest.mark.parametrize("missing", ["title", "assertion_id", "observation_kind"])
def test_example_does_not_relax_fields_missing_in_real_planner_output(missing):
    body = json.loads((ROOT / "skills" / EXAMPLE).read_text())["body"]
    journey = body["user_journeys"][0]
    row = journey if missing == "title" else journey["assertions"][0]
    row.pop(missing)

    errors = _product_acceptance_spec_errors(
        body, task_map={}, manifest={}, flow_kind="prd",
    )

    assert len(errors) == 1
    assert errors[0]["code"] == "product_acceptance_spec_invalid"
    assert missing in errors[0]["message"]


def test_single_task_candidate_keeps_product_coverage_with_empty_integration_checks():
    body = json.loads((ROOT / "skills" / EXAMPLE).read_text())["body"]
    task = {"task_id": "TASK-ONE", "acceptance_criteria": [{"id": "AC-SAVE"}]}
    task_map = {"tasks": [task]}
    body["assembly_owner"] = body["entrypoints"][0]["owner"] = task["task_id"]
    body["integration_checks"] = []
    reader, = body["candidate_reader_assignments"]
    reader.update(acceptance=[{"task_id": "TASK-ONE", "acceptance_id": "AC-SAVE"}],
                  integration_check_ids=[], plan_reads={"task_map": ["$.tasks[0]"]})
    assert _product_acceptance_spec_errors(body, task_map=task_map, manifest={}, flow_kind="prd") == []
    body["integration_checks"] = [{"check_id": "C1", "task_ids": ["TASK-ONE"], "assertion": "saved"}]
    errors = _product_acceptance_spec_errors(body, task_map=task_map, manifest={}, flow_kind="prd")
    assert any("integration_checks: []" in error["message"] for error in errors)
    body["integration_checks"] = []
    reader["journey_ids"] = []
    assert _product_acceptance_spec_errors(body, task_map=task_map, manifest={}, flow_kind="prd")
