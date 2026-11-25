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
