from __future__ import annotations

import pytest

from zf.runtime.goal_closure_result import (
    GoalClosureResultError,
    claim_set_issues,
    validate_goal_closure_result,
)


def _result(*, verdict: str = "passed") -> dict:
    status = "closed" if verdict == "passed" else "open"
    return {
        "schema_version": "goal-closure-result.v1",
        "workflow_run_id": "run-1",
        "goal_id": "GOAL-1",
        "flow_kind": "prd",
        "task_map_generation": "generation-1",
        "target_commit": "a" * 40,
        "objective_ref": "docs/prd.md",
        "goal_claim_set_ref": "goal-closure/claim-sets/claims.json",
        "goal_claim_set_digest": "b" * 64,
        "planning_result_ref": "artifacts/task-map.json",
        "candidate_ref": "candidate/GOAL-1",
        "closure_fact_ref": "goal-closure/facts/fact.json",
        "closure_fact_digest": "c" * 64,
        "input_result_refs": ["call-results/verify.json"],
        "goal_coverage": [{
            "goal_claim_id": "GOAL-AC-1",
            "status": status,
            "supporting_result_refs": (
                ["call-results/verify.json"] if status == "closed" else []
            ),
        }],
        "open_gap_refs": [] if verdict == "passed" else ["gaps/GAP-1.json"],
        "verdict": verdict,
        "recommended_action": "complete" if verdict == "passed" else "gap_plan",
        "summary": "all mandatory claims are closed",
    }


def test_goal_closure_result_accepts_complete_claim_coverage() -> None:
    validate_goal_closure_result(_result())


def test_goal_closure_result_rejects_pass_with_open_claim() -> None:
    result = _result()
    result["goal_coverage"][0]["status"] = "open"
    result["goal_coverage"][0]["supporting_result_refs"] = []

    with pytest.raises(GoalClosureResultError, match="open or blocked"):
        validate_goal_closure_result(result)


def test_claim_set_requires_mandatory_claims_but_allows_optional_coverage() -> None:
    result = _result()
    result["goal_coverage"].append({
        "goal_claim_id": "GOAL-OPTIONAL",
        "status": "closed",
        "supporting_result_refs": ["call-results/verify.json"],
    })
    claim_set = {
        "claim_set_digest": "b" * 64,
        "claims": [
            {"goal_claim_id": "GOAL-AC-1", "mandatory": True},
            {"goal_claim_id": "GOAL-OPTIONAL", "mandatory": False},
        ],
    }

    assert claim_set_issues(
        result,
        claim_set,
        admitted_result_refs={"call-results/verify.json"},
    ) == []


def test_claim_set_rejects_unknown_and_unadmitted_support() -> None:
    result = _result()
    result["goal_coverage"][0]["goal_claim_id"] = "GOAL-UNKNOWN"
    claim_set = {
        "claim_set_digest": "b" * 64,
        "claims": [{"goal_claim_id": "GOAL-AC-1", "mandatory": True}],
    }

    codes = {
        issue["code"]
        for issue in claim_set_issues(
            result,
            claim_set,
            admitted_result_refs=set(),
        )
    }
    assert codes == {"missing_claim", "unknown_claim", "result_not_admitted"}
