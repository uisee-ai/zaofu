"""A concrete source-to-task grouping fits existing policy without losing IDs.

Semantic appropriateness remains a Planner/Critic decision, not a runtime gate.
"""
from zf.core.config.schema import WorkflowWorkUnitsConfig, WorkflowSplitQualityConfig
from zf.runtime.writer_fanout_admission import writer_task_map_policy_errors


def test_ten_source_cases_remain_individually_mapped_under_eight_task_criteria():
    groups = [["AC-01", "AC-02", "AC-03"], ["AC-04", "AC-05"], ["AC-06"],
              ["AC-07"], ["AC-08"], ["AC-09"], ["AC-10"]]
    criteria = [{"id": f"TAC-{i}", "source_refs": source} for i, source in enumerate(groups, 1)]
    matrix = [{"source_id": source, "task_id": "READ", "acceptance_id": row["id"]}
              for row in criteria for source in row["source_refs"]]
    policy = WorkflowWorkUnitsConfig(enabled=True, split_quality=WorkflowSplitQualityConfig(
        mode="blocking", max_acceptance_criteria=8))
    task = {"task_id": "READ", "allowed_paths": ["readme.py", "test_readme.py", "README.md"],
            "acceptance_criteria": criteria}
    assert writer_task_map_policy_errors([task], work_units_config=policy) == []
    assert sorted(row["source_id"] for row in matrix) == [f"AC-{i:02d}" for i in range(1, 11)]
    # The same policy still rejects a flat 10-criterion Task; no cap was raised.
    task["acceptance_criteria"] = [{"id": row["source_id"]} for row in matrix]
    assert any("10 acceptance criteria, max is 8" in error
               for error in writer_task_map_policy_errors([task], work_units_config=policy))
