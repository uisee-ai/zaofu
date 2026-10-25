from copy import deepcopy

import pytest

from zf.runtime.plan_modules import plan_modules
from zf.runtime.task_map import build_task_map_from_ingest_plan, validate_task_map_payload


def task_map():
    return {
        "schema_version": "task-map.v1",
        "tasks": [{"task_id": name, "verification": "python -m pytest"} for name in ("T1", "T2")],
        "goal_claims": [{"goal_claim_id": "R1", "text": "共享需求"}],
        "modules": [
            {"module_id": "shared", "title": "共享能力", "task_ids": ["T1"], "goal_claim_ids": ["R1"]},
            {"module_id": "feature", "title": "领域能力", "task_ids": ["T1", "T2"], "design_refs": ["docs/design.md#feature"]},
        ],
    }


def test_shared_tasks_preserve_one_task_identity_through_admission():
    source = task_map()
    before = deepcopy(source)
    assert validate_task_map_payload(source).passed
    assert len(plan_modules(source)["modules"]) == 2
    assert len(source["tasks"]) == 2
    assert source == before


@pytest.mark.parametrize("change, reason", [
    ({"task_ids": ["UNKNOWN"]}, "unknown id"),
    ({"goal_claim_ids": ["UNKNOWN"]}, "unknown id"),
    ({"parent_module_id": "shared"}, "cyclic containment"),
    ({"parent_module_id": "UNKNOWN"}, "unknown parent"),
    ({"task_ids": ["T1", "T1"]}, "duplicate references"),
    ({"title": ""}, "title must"),
])
def test_invalid_module_relations_are_rejected_by_task_map_caller(change, reason):
    source = task_map()
    source["modules"][0].update(change)
    result = validate_task_map_payload(source)
    assert not result.passed
    assert any(reason in error for error in result.errors)
    assert plan_modules(source)["source"] == "invalid"


def test_legacy_task_map_does_not_invent_modules_from_phase():
    assert plan_modules({"tasks": [{"task_id": "T1", "phase": "impl"}]})["modules"] == []


def test_ingest_preserves_explicit_modules():
    source = task_map()
    result = build_task_map_from_ingest_plan(source, source_refs={})
    assert result["modules"] == source["modules"]
    assert result["goal_claims"] == source["goal_claims"]
