"""Replacement input consumption must remain an explicit producer decision."""
from copy import deepcopy

import pytest

from zf.runtime.module_gap_plan import build_gap_task_map_amend


SELECTION = {
    "schema_version": "task-plan-input-selection.v1",
    "implementation": ["task_execution_input"],
    "task-verify": ["task_execution_input"],
}


def amend(*, predecessor_selected=True, replacement_selection=None):
    base = {"tasks": [{"task_id": "OLD", "owner_role": "dev", "allowed_paths": ["app/**"],
        "evidence_contract": {"plan_input_selection": deepcopy(SELECTION)} if predecessor_selected else {}}]}
    gap = {"task_id": "NEW", "owner_role": "dev", "claim_paths": ["app/**"],
           "acceptance": ["preserve behavior"], "verify_commands": ["pytest"],
           "source_refs": ["event:gap"], "supersedes_task_ids": ["OLD"]}
    if replacement_selection is not None:
        gap["evidence_contract"] = {"plan_input_selection": deepcopy(replacement_selection)}
    frozen = deepcopy((base, gap))
    try:
        return build_gap_task_map_amend(base, gap_tasks=[gap], supersedes_task_map_ref="base.json")
    finally:
        assert (base, gap) == frozen


def test_replacement_cannot_silently_drop_reviewed_consumption_contract():
    with pytest.raises(ValueError, match="plan_input_selection"):
        amend()


@pytest.mark.parametrize("expanded", [False, True])
def test_explicit_replacement_selection_is_preserved_without_runtime_semantic_choice(expanded):
    policy = deepcopy(SELECTION)
    if expanded:
        policy["implementation"].append("source_index")
        policy["task-verify"].append("acceptance_matrix")
    task = amend(replacement_selection=policy)["tasks"][0]
    assert task["evidence_contract"]["plan_input_selection"] == policy
    assert task["acceptance"] == ["preserve behavior"]


def test_legacy_replacement_retains_existing_full_read_policy():
    task = amend(predecessor_selected=False)["tasks"][0]
    assert "plan_input_selection" not in task["evidence_contract"]
