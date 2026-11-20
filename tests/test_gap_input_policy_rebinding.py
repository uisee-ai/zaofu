from copy import deepcopy

import pytest

from zf.runtime.gap_input_policy import rebind_gap_input_policy
from zf.runtime.module_gap_plan import build_gap_task_map_amend


def test_gap_amend_rebinds_declared_task_paths_by_identity():
    base = {"schema_version": "task-map.v1", "tasks": [
        {"task_id": k, "blocked_by": []} for k in ("A", "B", "C")],
        "task_input_policy": {"schema_version": "task-input-policy.v1",
            "shared_paths": ["$.schema_version"], "tasks": [
                {"task_id": k, "paths": [f"$.tasks[{i}]"]}
                for i,k in enumerate(("A", "B", "C"))]}}
    original = deepcopy(base)
    gaps = [{"task_id": k, "parent_task_id": "B", "claim_paths": [f"{k}/**"],
             "acceptance": ["actual behavior"], "verify_commands": ["pytest"],
             "source_refs": ["event:gap"], **extra} for k,extra in (
                 ("UPSTREAM", {}), ("REPLACEMENT", {"supersedes_task_ids": ["B"],
                                                   "blocked_by": ["UPSTREAM"]}))]
    out = build_gap_task_map_amend(base, gap_tasks=gaps, supersedes_task_map_ref="base.json")
    policy = out["task_input_policy"]
    rows = {row["task_id"]:row["paths"] for row in policy["tasks"]}
    assert set(rows) == {"A", "C", "UPSTREAM", "REPLACEMENT"}
    for i,task in enumerate(out["tasks"]):
        assert f"$.tasks[{i}]" in rows[task["task_id"]]
    assert rows["C"] == ["$.tasks[1]"]
    assert {"$.source_refs", "$.amend"} <= set(policy["shared_paths"])
    assert base == original


def test_dependency_and_shared_task_paths_follow_identity():
    base = {"tasks": [{"task_id": "A"}, {"task_id": "B"}],
            "task_input_policy": {"schema_version": "task-input-policy.v1",
                "shared_paths": ["$.tasks[1].title"], "tasks": [
                    {"task_id": "A", "paths": ["$.tasks[0]", "$.tasks[1].contract"]},
                    {"task_id": "B", "paths": ["$.tasks[1]"]}]}}
    amended = {"tasks": [{"task_id": "A"}, {"task_id": "NEW"}]}
    rebind_gap_input_policy(base, amended, gap_tasks=[{"task_id": "NEW", "parent_task_id": "B"}],
                            replacements={"B": ["NEW"]})
    assert amended["task_input_policy"]["tasks"][0]["paths"] == ["$.tasks[0]", "$.tasks[1].contract"]
    assert amended["task_input_policy"]["shared_paths"][0] == "$.tasks[1].title"
    replay = deepcopy(amended)
    rebind_gap_input_policy(amended, replay, gap_tasks=[{"task_id": "NEW", "parent_task_id": "B"}],
                            replacements={"B": ["NEW"]})
    assert replay == amended


def test_missing_parent_partition_does_not_grant_whole_plan():
    base = {"tasks": [{"task_id": "A"}], "task_input_policy": {
        "schema_version": "task-input-policy.v1", "shared_paths": [],
        "tasks": [{"task_id": "A", "paths": ["$.tasks[0]"]}]}}
    amended = {"tasks": [{"task_id": "A"}, {"task_id": "NEW"}]}
    with pytest.raises(ValueError, match="no explicit source input partition"):
        rebind_gap_input_policy(base, amended, gap_tasks=[{"task_id": "NEW"}], replacements={})
    assert "task_input_policy" not in amended
