from __future__ import annotations

import json

from zf.runtime.replan_contract_eval import (
    evaluate_replan_contract,
    event_payload_for_eval,
)


def _task_map(*, task_id: str = "TASK-A", scope: str = "src/a.py") -> dict:
    return {
        "schema_version": "task-map.v1",
        "feature_id": "F-REPLAN",
        "tasks": [
            {
                "task_id": task_id,
                "title": "修复调度重试",
                "behavior": "修复调度重试后重复采纳的问题",
                "owner_role": "dev",
                "wave": 1,
                "scope": [scope],
                "exclusive_files": [scope],
                "verification": "uv run pytest tests/test_replan.py -q",
                "verification_tiers": ["runtime"],
                "acceptance": ["重复采纳不会创建重复任务"],
            }
        ],
    }


def _source_index(*, task_id: str = "TASK-A") -> dict:
    return {
        "schema_version": "source-index.v1",
        "feature_id": "F-REPLAN",
        "tasks": [
            {
                "task_id": task_id,
                "source_key": "docs/design/81.md#gate",
                "source_ref": "docs/design/81.md#gate",
                "source_excerpt": "修复调度重试后重复采纳的问题。",
                "source_mode": "canonical",
            }
        ],
    }


def test_replan_contract_eval_schema_round_trip() -> None:
    result = evaluate_replan_contract(
        new_task_map=_task_map(),
        source_index=_source_index(),
        eval_id="eval-1",
        old_task_map_ref="tm-v1",
        new_task_map_ref="tm-v2",
    )

    data = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))

    assert data["schema_version"] == "replan-contract-eval.v1"
    assert data["eval_id"] == "eval-1"
    assert data["new_task_map_ref"] == "tm-v2"
    assert {check["name"] for check in data["checks"]} >= {
        "schema_completeness",
        "contract_completeness",
        "scope_concurrency",
        "source_coverage_no_invention",
        "profile_policy",
    }


def test_replan_eval_adopts_valid_baseline_bundle() -> None:
    result = evaluate_replan_contract(
        new_task_map=_task_map(),
        source_index=_source_index(),
        profile="baseline",
    )

    assert result.decision == "adopt"
    assert result.summary["failed_check_count"] == 0


def test_replan_eval_revises_incomplete_task_contract() -> None:
    task_map = _task_map()
    raw = task_map["tasks"][0]
    raw.pop("behavior")
    raw.pop("owner_role")
    raw.pop("verification")
    raw.pop("acceptance")

    result = evaluate_replan_contract(
        new_task_map=task_map,
        source_index=_source_index(),
    )

    assert result.decision == "revise"
    assert "contract_completeness" in result.summary["failed_checks"]
    assert any("owner_role" in item for item in result.required_fixes)


def test_replan_eval_revises_unsafe_same_wave_overlap() -> None:
    task_map = _task_map()
    task_map["tasks"].append({
        "task_id": "TASK-B",
        "title": "同波次修改相同文件",
        "behavior": "修改同一个模块",
        "owner_role": "dev",
        "wave": 1,
        "scope": ["src/a.py"],
        "exclusive_files": ["src/a.py"],
        "verification": "uv run pytest tests/test_replan_b.py -q",
        "verification_tiers": ["runtime"],
        "acceptance": ["B works"],
    })
    source_index = _source_index()
    source_index["tasks"].append({
        "task_id": "TASK-B",
        "source_key": "docs/design/81.md#b",
        "source_ref": "docs/design/81.md#b",
        "source_excerpt": "修改同一个模块。",
        "source_mode": "canonical",
    })

    result = evaluate_replan_contract(
        new_task_map=task_map,
        source_index=source_index,
    )

    assert result.decision == "revise"
    assert "scope_concurrency" in result.summary["failed_checks"]


def test_replan_eval_projects_contract_delta() -> None:
    old_task_map = {
        "schema_version": "task-map.v1",
        "feature_id": "F-REPLAN",
        "tasks": [
            _task_map()["tasks"][0],
            {
                **_task_map(task_id="TASK-C", scope="src/c.py")["tasks"][0],
                "title": "旧任务 C",
            },
        ],
    }
    new_task_map = {
        "schema_version": "task-map.v1",
        "feature_id": "F-REPLAN",
        "tasks": [
            _task_map()["tasks"][0],
            {
                **_task_map(task_id="TASK-C", scope="src/c.py")["tasks"][0],
                "title": "重写任务 C",
            },
            _task_map(task_id="TASK-D", scope="src/d.py")["tasks"][0],
        ],
    }
    source_index = _source_index()
    source_index["tasks"].extend([
        {
            "task_id": "TASK-C",
            "source_key": "docs/design/81.md#c",
            "source_ref": "docs/design/81.md#c",
            "source_excerpt": "重写任务 C。",
            "source_mode": "canonical",
        },
        {
            "task_id": "TASK-D",
            "source_key": "docs/design/81.md#d",
            "source_ref": "docs/design/81.md#d",
            "source_excerpt": "新增任务 D。",
            "source_mode": "canonical",
        },
    ])

    result = evaluate_replan_contract(
        old_task_map=old_task_map,
        new_task_map=new_task_map,
        source_index=source_index,
    )

    assert result.contract_delta["preserve_task_ids"] == ["TASK-A"]
    assert result.contract_delta["rewrite_task_ids"] == ["TASK-C"]
    assert result.contract_delta["new_task_ids"] == ["TASK-D"]


def test_replan_eval_revises_missing_source_index_coverage() -> None:
    result = evaluate_replan_contract(new_task_map=_task_map(), source_index={})

    assert result.decision == "revise"
    assert "source_coverage_no_invention" in result.summary["failed_checks"]
    assert any("source_index missing task_id" in item for item in result.required_fixes)


def test_replan_eval_revises_no_invention_violation() -> None:
    task_map = _task_map()
    task_map["tasks"][0]["untraced"] = True

    result = evaluate_replan_contract(
        new_task_map=task_map,
        source_index=_source_index(),
    )

    assert result.decision == "revise"
    assert any("no_invention" in item for item in result.required_fixes)


def test_replan_eval_requires_done_evidence_carry_forward() -> None:
    result = evaluate_replan_contract(
        old_tasks={"TASK-A": {"id": "TASK-A", "status": "done"}},
        new_task_map=_task_map(),
        source_index=_source_index(),
    )

    assert result.decision == "revise"
    assert "done_evidence_carry_forward" in result.summary["failed_checks"]


def test_replan_eval_requires_resume_safety_for_active_rewrite() -> None:
    result = evaluate_replan_contract(
        old_task_map=_task_map(scope="src/old.py"),
        old_tasks={"TASK-A": {"id": "TASK-A", "status": "in_progress"}},
        new_task_map=_task_map(scope="src/new.py"),
        source_index=_source_index(),
    )

    assert result.decision == "revise"
    assert "resume_safety" in result.summary["failed_checks"]


def test_replan_eval_event_payload_is_refs_only() -> None:
    result = evaluate_replan_contract(
        new_task_map=_task_map(),
        source_index=_source_index(),
        artifact_ref=".zf/artifacts/F-REPLAN/replan-eval.json",
        eval_id="eval-refs",
    )

    payload = event_payload_for_eval(result)

    assert payload["eval_id"] == "eval-refs"
    assert payload["refs"]["artifact_ref"].endswith("replan-eval.json")
    assert "checks" not in payload
    assert "tasks" not in json.dumps(payload, ensure_ascii=False)


def test_replan_baseline_skips_independent_critic_requirement() -> None:
    result = evaluate_replan_contract(
        new_task_map=_task_map(),
        source_index=_source_index(),
        profile="baseline",
    )

    assert result.decision == "adopt"
    assert "profile_policy" not in result.summary["failed_checks"]


def test_replan_strict_requires_independent_review_evidence() -> None:
    result = evaluate_replan_contract(
        new_task_map=_task_map(),
        source_index=_source_index(),
        profile="strict",
    )

    assert result.decision == "revise"
    assert "profile_policy" in result.summary["failed_checks"]


def test_replan_release_requires_ship_boundary_evidence() -> None:
    result = evaluate_replan_contract(
        new_task_map=_task_map(),
        source_index=_source_index(),
        profile="release",
        strict_review_evidence={
            "critic_ref": ".zf/artifacts/critic.json",
            "verifier_ref": ".zf/artifacts/verifier.json",
        },
    )

    assert result.decision == "reject"
    assert "profile_policy" in result.summary["failed_checks"]
