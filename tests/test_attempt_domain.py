from __future__ import annotations

from zf.runtime.attempt_domain import (
    feedback_matches_attempt,
    infer_attempt_domain,
)


def test_attempt_domain_legacy_inference_is_stage_scoped():
    assert infer_attempt_domain(stage_id="prd-plan") == "plan"
    assert infer_attempt_domain(stage_id="impl-assembly") == "candidate"
    assert infer_attempt_domain(stage_id="goal-gap-replan") == "gap"
    assert infer_attempt_domain(stage_id="run-manager-recovery") == "recovery"
    assert infer_attempt_domain(stage_id="impl", payload={"task_id": "T1"}) == "task"


def test_feedback_does_not_cross_domain_generation_or_package():
    feedback = {
        "attempt_domain": "plan",
        "task_id": "T1",
        "task_map_generation": "g1",
        "plan_artifact_package_digest": "p1",
    }
    assert not feedback_matches_attempt(feedback, {
        "attempt_domain": "task",
        "task_id": "T1",
        "task_map_generation": "g1",
        "plan_artifact_package_digest": "p1",
    })
    assert not feedback_matches_attempt(feedback, {
        "attempt_domain": "plan",
        "task_id": "T1",
        "task_map_generation": "g2",
        "plan_artifact_package_digest": "p2",
    })


def test_legacy_feedback_with_blank_identity_remains_readable():
    assert feedback_matches_attempt(
        {"task_id": "T1"},
        {
            "attempt_domain": "task",
            "task_id": "T1",
            "task_map_generation": "g2",
        },
    )
