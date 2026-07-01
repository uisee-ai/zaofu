from __future__ import annotations

from zf.runtime.module_gap_plan import (
    build_gap_task_map_amend,
    validate_module_gap_plan_payload,
)
from zf.runtime.task_map import validate_task_map_payload


def test_module_gap_plan_validation_rejects_incomplete_gap_task() -> None:
    result = validate_module_gap_plan_payload({
        "schema_version": "module-gap-plan.v1",
        "gap_tasks": [{
            "task_id": "CANGJIE-WEB-GAP-001",
            "source_refs": ["hermes-agent/web"],
        }],
    })

    assert result.passed is False
    assert "CANGJIE-WEB-GAP-001.claim_paths is required" in result.errors
    assert "CANGJIE-WEB-GAP-001.acceptance is required" in result.errors
    assert "CANGJIE-WEB-GAP-001.verify_commands is required" in result.errors


def test_gap_tasks_append_to_full_task_map_as_canonical_tasks() -> None:
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "CANGJIE",
        "source_refs": {"source_index_ref": ".zf/artifacts/CANGJIE/source_index.json"},
        "tasks": [{
            "task_id": "CANGJIE-WEB-001",
            "title": "Web baseline",
            "owner_role": "dev",
            "wave": 0,
            "allowed_paths": ["web/**"],
            "allowed_paths_reason": "original web slice",
            "acceptance": ["baseline web slice exists"],
        }],
    }
    gap_task = {
        "task_id": "CANGJIE-WEB-GAP-001",
        "module_id": "web-dashboard",
        "parent_task_id": "CANGJIE-WEB-001",
        "affinity_tag": "web-tui",
        "owner_role": "dev",
        "claim_paths": ["web/src/**", "packages/web-adapter/**"],
        "acceptance": ["WebChat reaches Cangjie runtime"],
        "verify_commands": ["npm run test:e2e:webchat"],
        "source_refs": ["hermes-agent/web"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[gap_task],
        supersedes_task_map_ref=".zf/artifacts/CANGJIE/task_map.json",
        gap_plan_ref="docs/validation/cangjie-gap-task-map.json",
    )

    assert amended["amend"]["gap_task_ids"] == ["CANGJIE-WEB-GAP-001"]
    assert amended["source_refs"]["supersedes_task_map_ref"] == ".zf/artifacts/CANGJIE/task_map.json"
    appended = amended["tasks"][-1]
    assert appended["task_id"] == "CANGJIE-WEB-GAP-001"
    assert appended["parent_task_id"] == "CANGJIE-WEB-001"
    assert appended["allowed_paths"] == ["web/src/**", "packages/web-adapter/**"]
    assert validate_task_map_payload(amended).passed is True
