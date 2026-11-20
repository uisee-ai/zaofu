from copy import deepcopy

from zf.runtime.module_gap_plan import build_gap_task_map_amend


def test_gap_amend_preserves_structured_acceptance_and_verification_ownership():
    criteria = [{
        "id": "AC-GAP", "statement": "Reject unsafe YAML object tags.",
        "mandatory": True, "verification_owner": "task_verify",
        "verification_tier": "runtime", "verification_command_ids": ["VC-GAP"],
        "source_acceptance_ids": ["AC-CONFIG"],
    }]
    gap = {
        "task_id": "GAP", "allowed_paths": ["app/deps.txt"],
        "acceptance_criteria": deepcopy(criteria), "source_refs": ["event:failure"],
        "verify_commands": ["pytest tests/test_yaml.py"],
    }
    result = build_gap_task_map_amend(
        {"schema_version": "task-map.v1", "tasks": []},
        gap_tasks=[gap], supersedes_task_map_ref="artifacts/original.json",
    )
    task = result["tasks"][0]
    assert task["acceptance_criteria"] == criteria
    assert task["acceptance"] == [criteria[0]["statement"]]
    task["acceptance_criteria"][0]["verification_command_ids"].append("MUTATED")
    assert gap["acceptance_criteria"] == criteria
