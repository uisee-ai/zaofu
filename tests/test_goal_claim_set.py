from __future__ import annotations

import pytest

from zf.runtime.goal_claim_set import (
    GoalClaimSetError,
    build_goal_claim_set,
    canonical_task_map_generation,
    pin_goal_claim_set_from_task_map,
)


def test_canonical_task_map_generation_preserves_explicit_and_normalizes_legacy() -> None:
    assert canonical_task_map_generation(
        task_map_generation="generation-1",
        task_map_digest="ignored",
    ) == "generation-1"
    from_digest = canonical_task_map_generation(task_map_digest="digest-1")
    from_ref = canonical_task_map_generation(task_map_ref="artifacts/task-map.json")
    assert len(from_digest) == 64
    assert len(from_ref) == 64
    assert from_digest != from_ref


def test_goal_claim_set_is_stable_for_task_acceptance_fallback() -> None:
    task_map = {
        "tasks": [{
            "task_id": "TASK-1",
            "acceptance_criteria": [
                "AC-LOGIN: expired sessions return 401",
                "fresh sessions remain valid",
            ],
        }],
    }
    first = build_goal_claim_set(
        task_map,
        workflow_run_id="run-1",
        goal_id="GOAL-1",
        task_map_generation="generation-1",
    )
    replay = build_goal_claim_set(
        task_map,
        workflow_run_id="run-1",
        goal_id="GOAL-1",
        task_map_generation="generation-1",
    )

    assert first == replay
    assert first["claim_set_digest"]
    assert [item["goal_claim_id"] for item in first["claims"]] == [
        "AC-LOGIN",
        first["claims"][1]["goal_claim_id"],
    ]
    assert first["claims"][1]["goal_claim_id"].startswith("TASK-1-AC2-")


def test_explicit_goal_claims_take_precedence_over_task_fallback() -> None:
    claim_set = build_goal_claim_set(
        {
            "goal_claims": [{
                "id": "GOAL-PROVIDER",
                "text": "provider call succeeds",
                "mandatory": False,
            }],
            "tasks": [{
                "task_id": "TASK-IGNORED",
                "acceptance_criteria": ["must not become a top-level claim"],
            }],
        },
        workflow_run_id="run-1",
        goal_id="GOAL-1",
        task_map_generation="generation-1",
    )

    assert claim_set["source"] == "task_map.goal_claims"
    assert claim_set["claims"] == [{
        "goal_claim_id": "GOAL-PROVIDER",
        "text": "provider call succeeds",
        "mandatory": False,
        "source_ref": "",
    }]


def test_duplicate_explicit_goal_claim_ids_fail_closed() -> None:
    with pytest.raises(GoalClaimSetError, match="duplicate goal claim id"):
        build_goal_claim_set(
            {
                "goal_claims": [
                    {"id": "GOAL-DUP", "text": "first"},
                    {"id": "GOAL-DUP", "text": "second"},
                ],
            },
            workflow_run_id="run-1",
            goal_id="GOAL-1",
            task_map_generation="generation-1",
        )


def test_pin_goal_claim_set_keeps_confirmed_objective_acceptance(
    tmp_path,
) -> None:
    task_map = tmp_path / "artifacts" / "task-map.json"
    objective = tmp_path / "artifacts" / "requirements.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        '{"tasks":[{"task_id":"TASK-1","acceptance_criteria":["unit tests pass"]}]}',
        encoding="utf-8",
    )
    objective.write_text(
        '{"acceptance":["browser E2E passes","workflow reaches delivery"]}',
        encoding="utf-8",
    )

    claim_set, _ = pin_goal_claim_set_from_task_map(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        task_map_ref="artifacts/task-map.json",
        workflow_run_id="run-1",
        goal_id="GOAL-1",
        task_map_generation="generation-1",
        objective_ref="artifacts/requirements.json",
    )

    assert claim_set["source"] == (
        "objective.acceptance+task_map.acceptance_criteria_fallback"
    )
    assert [claim["text"] for claim in claim_set["claims"]] == [
        "browser E2E passes",
        "workflow reaches delivery",
        "unit tests pass",
    ]
    assert all(claim["mandatory"] for claim in claim_set["claims"])
