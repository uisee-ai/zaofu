"""Calibrate mechanical rejection separately from semantic review obligations."""

import json
from pathlib import Path

import pytest

from zf.runtime.task_map import validate_task_map_payload


CASES = json.loads((Path(__file__).parent / "fixtures/plan-counterexamples/cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_counterexample_has_the_declared_mechanical_result(case):
    result = validate_task_map_payload(case["task_map"])
    assert result.passed is case["expected_kernel_passed"], result.errors
    if case["error_contains"]:
        assert any(case["error_contains"] in error for error in result.errors)
    if case["diagnostic"]:
        assert any(d["code"] == case["diagnostic"] for d in result.summary["goal_coverage"]["diagnostics"])


def test_mechanically_valid_plan_is_not_evidence_of_semantic_completeness():
    case = next(c for c in CASES if c["id"] == "semantic_narrowing")
    assert validate_task_map_payload(case["task_map"]).passed
    assert case["expected_critic_verdict"] == "reject"
    # This fixture defines an independent review expectation, not an agent verdict.
    assert "401" in case["source_requirement"] and "401" in case["required_finding"]
