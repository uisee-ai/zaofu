from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.goal_coverage_graph import build_goal_coverage_graph
from tests.test_goal_coverage_graph import (
    _admission_event, _closure_result, _task_map, _tasks, _verification_result,
)


@pytest.mark.parametrize("case,expected", [
    ("integrated", "passed"), ("other_run", "stale"),
    ("missing_task", "stale"), ("old_commit", "stale"),
    ("candidate_verify", "stale"), ("old_generation", "stale"),
])
def test_task_verification_uses_exact_current_candidate_lineage(case, expected):
    task_ref = ZfEvent(type="task.ref.updated", task_id="TASK-A", payload={
        "workflow_run_id": "OTHER" if case == "other_run" else "RUN-1",
        "source_commit": "task-commit",
    })
    candidate = ZfEvent(type="candidate.ready", payload={
        "workflow_run_id": "RUN-1", "candidate_head_commit": "abc123",
        "completed_task_ids": [] if case == "missing_task" else ["TASK-A"],
    })
    result = _verification_result(
        target_commit="old-task-commit" if case == "old_commit" else "task-commit",
        verification_owner="candidate_verify" if case == "candidate_verify" else "task_verify",
        task_map_generation="GEN-1" if case == "old_generation" else "GEN-2",
    )
    verify = ZfEvent(type="task.pipeline.verify.completed", task_id="TASK-A",
                     payload={"verification_result": result})
    closure = ZfEvent(type="goal.closure.synthesized", payload={
        "goal_closure_result": _closure_result(),
    })
    rows = [task_ref, verify, _admission_event(verify.id), candidate, closure,
            _admission_event(closure.id, schema="goal-closure-result.v1", task_id=None)]
    graph = build_goal_coverage_graph(
        task_map=_task_map(), tasks=_tasks(), events=list(enumerate(rows, start=1)),
        project_id="zaofu", feature_id="F-GOAL", task_map_ref="artifacts/task_map.json",
    )
    claim = next(n for n in graph["nodes"] if n.get("goal_claim_id") == "CLAIM-A")
    assert claim["task_verification"] == expected
    assert graph["summary"]["claims_with_current_results"] == (1 if expected == "passed" else 0)
