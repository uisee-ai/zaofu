from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.goal_gap_plan import (
    build_gap_task_map_amend,
    gap_tasks_from_gap_plan_payload,
    validate_goal_gap_plan_payload,
    write_gap_task_map_amend_artifact,
)
from zf.runtime.task_map import validate_task_map_payload


def _base_task_map() -> dict:
    return {
        "schema_version": "task-map.v1",
        "feature_id": "ISSUE-123",
        "source_refs": {"source_index_ref": ".zf/artifacts/ISSUE-123/source_index.json"},
        "tasks": [{
            "task_id": "ISSUE-123-PLAN-001",
            "title": "Initial implementation",
            "owner_role": "dev",
            "wave": 0,
            "allowed_paths": ["src/**"],
            "allowed_paths_reason": "initial issue implementation",
            "acceptance": ["baseline issue behavior exists"],
        }],
    }


def test_goal_gap_plan_validation_accepts_generic_issue_gap() -> None:
    payload = {
        "schema_version": "goal-gap-plan.v1",
        "goal_id": "ISSUE-123",
        "goal_kind": "issue",
        "gap_category": "issue_gap",
        "affected_tasks": ["ISSUE-123-PLAN-001"],
        "gate_changes": ["require api regression evidence"],
        "gap_tasks": [{
            "task_id": "ISSUE-123-GAP-001",
            "claim_paths": ["src/api/**", "tests/api/**"],
            "acceptance": ["API returns the requested issue state"],
            "verify_commands": ["uv run pytest tests/api/test_issue_123.py"],
            "source_refs": ["issues/123.md"],
        }],
    }

    result = validate_goal_gap_plan_payload(payload)
    gap_tasks = gap_tasks_from_gap_plan_payload(payload)

    assert result.passed is True
    assert result.summary["task_ids"] == ["ISSUE-123-GAP-001"]
    assert gap_tasks[0]["goal_kind"] == "issue"
    assert gap_tasks[0]["gap_category"] == "issue_gap"
    assert gap_tasks[0]["affected_tasks"] == ["ISSUE-123-PLAN-001"]
    assert gap_tasks[0]["gate_changes"] == ["require api regression evidence"]


def test_goal_gap_plan_validation_accepts_prd_and_refactor_categories() -> None:
    for goal_kind, gap_category in (
        ("prd", "acceptance_gap"),
        ("refactor", "parity_gap"),
    ):
        payload = {
            "schema_version": "goal-gap-plan.v1",
            "goal_id": f"{goal_kind.upper()}-1",
            "goal_kind": goal_kind,
            "gap_category": gap_category,
            "gap_tasks": [{
                "task_id": f"{goal_kind.upper()}-GAP-001",
                "claim_paths": ["src/**", "tests/**"],
                "acceptance": ["gap behavior is closed"],
                "verify_commands": ["uv run pytest tests"],
                "source_refs": [f"docs/{goal_kind}.md"],
            }],
        }

        result = validate_goal_gap_plan_payload(payload)

        assert result.passed is True
        assert result.errors == []


def test_goal_gap_plan_appends_generic_gap_task_to_canonical_task_map() -> None:
    payload = {
        "schema_version": "goal-gap-plan.v1",
        "goal_id": "ISSUE-123",
        "goal_kind": "issue",
        "gap_category": "issue_gap",
        "replan_history_ref": "docs/plans/ISSUE-123/replan-history.jsonl",
        "gap_tasks": [{
            "task_id": "ISSUE-123-GAP-001",
            "title": "Fill API regression gap",
            "parent_task_id": "ISSUE-123-PLAN-001",
            "affinity_tag": "api",
            "claim_paths": ["src/api/**", "tests/api/**"],
            "acceptance": ["API returns the requested issue state"],
            "verify_commands": ["uv run pytest tests/api/test_issue_123.py"],
            "source_refs": ["issues/123.md", "reports/ISSUE-123/gap.md"],
            "repro_ref": "reports/ISSUE-123/repro.md",
            "acceptance_id": "ISSUE-123-AC-API",
        }],
    }
    gap_tasks = gap_tasks_from_gap_plan_payload(payload)

    amended = build_gap_task_map_amend(
        _base_task_map(),
        gap_tasks=gap_tasks,
        supersedes_task_map_ref=".zf/artifacts/ISSUE-123/task_map.json",
        gap_plan_ref="reports/ISSUE-123/goal-gap-plan.json",
    )

    assert amended["amend"]["kind"] == "goal_gap"
    assert amended["amend"]["goal_kind"] == "issue"
    assert amended["amend"]["gap_category"] == "issue_gap"
    assert amended["amend"]["gap_task_ids"] == ["ISSUE-123-GAP-001"]
    appended = amended["tasks"][-1]
    assert appended["task_id"] == "ISSUE-123-GAP-001"
    assert appended["goal_kind"] == "issue"
    assert appended["gap_category"] == "issue_gap"
    assert appended["allowed_paths"] == ["src/api/**", "tests/api/**"]
    assert appended["evidence_contract"]["goal_id"] == "ISSUE-123"
    assert appended["evidence_contract"]["goal_kind"] == "issue"
    assert appended["evidence_contract"]["gap_category"] == "issue_gap"
    assert appended["evidence_contract"]["acceptance_id"] == "ISSUE-123-AC-API"
    assert appended["evidence_contract"]["repro_ref"] == "reports/ISSUE-123/repro.md"
    assert appended["evidence_contract"]["replan_history_ref"] == (
        "docs/plans/ISSUE-123/replan-history.jsonl"
    )
    assert appended["evidence_contract"]["source_refs"] == [
        "issues/123.md",
        "reports/ISSUE-123/gap.md",
    ]
    assert validate_task_map_payload(amended).passed is True


def test_goal_gap_plan_preserves_baseline_prompt_ref() -> None:
    base = _base_task_map()
    base["source_refs"]["prompt_ref"] = "docs/plans/original-prompt.md"
    gap_task = {
        "task_id": "ISSUE-123-GAP-001",
        "goal_kind": "issue",
        "gap_category": "issue_gap",
        "claim_paths": ["src/api/**"],
        "acceptance": ["missing behavior is implemented"],
        "verify_commands": ["uv run pytest tests/api/test_issue_123.py"],
        "source_refs": ["reports/ISSUE-123/gap.md"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[gap_task],
        supersedes_task_map_ref=".zf/artifacts/ISSUE-123/task_map.json",
        gap_plan_ref="reports/ISSUE-123/goal-gap-plan.json",
    )

    assert amended["source_refs"]["prompt_ref"] == "docs/plans/original-prompt.md"
    assert amended["source_refs"]["gap_plan_ref"] == "reports/ISSUE-123/goal-gap-plan.json"


def test_write_gap_task_map_amend_appends_goal_replan_history(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    project_root = tmp_path
    base_ref = ".zf/artifacts/ISSUE-123/task_map.json"
    base_path = state_dir / "artifacts" / "ISSUE-123" / "task_map.json"
    base_path.parent.mkdir(parents=True)
    base_path.write_text(json.dumps(_base_task_map()), encoding="utf-8")
    payload = {
        "schema_version": "goal-gap-plan.v1",
        "goal_id": "ISSUE-123",
        "goal_kind": "issue",
        "gap_category": "issue_gap",
        "replan_history_ref": "docs/plans/ISSUE-123/replan-history.jsonl",
        "affected_tasks": ["ISSUE-123-PLAN-001"],
        "gate_changes": ["require API regression evidence"],
        "gap_tasks": [{
            "task_id": "ISSUE-123-GAP-001",
            "claim_paths": ["src/api/**", "tests/api/**"],
            "acceptance": ["API returns the requested issue state"],
            "verify_commands": ["uv run pytest tests/api/test_issue_123.py"],
            "source_refs": ["issues/123.md"],
        }],
    }
    gap_tasks = gap_tasks_from_gap_plan_payload(payload)

    first = write_gap_task_map_amend_artifact(
        state_dir=state_dir,
        project_root=project_root,
        base_task_map_ref=base_ref,
        pdd_id="ISSUE-123",
        source_event_id="evt-gap-1",
        gap_tasks=gap_tasks,
        gap_plan_ref="reports/ISSUE-123/goal-gap-plan.json",
    )
    second = write_gap_task_map_amend_artifact(
        state_dir=state_dir,
        project_root=project_root,
        base_task_map_ref=base_ref,
        pdd_id="ISSUE-123",
        source_event_id="evt-gap-2",
        gap_tasks=gap_tasks,
        gap_plan_ref="reports/ISSUE-123/goal-gap-plan.json",
    )

    history_path = project_root / "docs/plans/ISSUE-123/replan-history.jsonl"
    rows = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
    ]
    assert first["replan_history_ref"] == "docs/plans/ISSUE-123/replan-history.jsonl"
    assert second["replan_history_path"] == str(history_path)
    assert [row["source_event_id"] for row in rows] == ["evt-gap-1", "evt-gap-2"]
    assert rows[0]["schema_version"] == "goal-replan-history-entry.v1"
    assert rows[0]["goal_kind"] == "issue"
    assert rows[0]["gap_category"] == "issue_gap"
    assert rows[0]["affected_tasks"] == ["ISSUE-123-PLAN-001"]
    assert rows[0]["gate_changes"] == ["require API regression evidence"]
