from __future__ import annotations

import pytest

from zf.runtime.module_gap_plan import (
    build_gap_task_map_amend,
    gap_tasks_from_gap_plan_payload,
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


def test_module_gap_plan_validation_rejects_overlapping_claim_paths() -> None:
    shared = {
        "claim_paths": ["app/src/render/scene.ts"],
        "acceptance": ["render behavior is correct"],
        "verify_commands": ["npm test"],
        "source_refs": ["app/src/render/scene.ts:10"],
    }

    result = validate_module_gap_plan_payload({
        "schema_version": "module-gap-plan.v1",
        "gap_tasks": [
            {"task_id": "GAP-RENDER", **shared},
            {"task_id": "GAP-CAMERA", **shared},
        ],
    })

    assert result.passed is False
    assert result.errors == [
        "gap tasks 'GAP-RENDER' and 'GAP-CAMERA' have overlapping "
        "claim_path 'app/src/render/scene.ts'",
    ]


def test_multi_gap_payload_scopes_aggregate_supersedes_by_parent() -> None:
    tasks = gap_tasks_from_gap_plan_payload({
        "goal_id": "PRD-1",
        "supersedes_task_ids": ["TASK-EVIDENCE"],
        "gap_tasks": [
            {
                "task_id": "TASK-DATA",
                "parent_task_id": "TASK-DATA-PUBLISH",
                "claim_paths": ["data/**"],
                "acceptance": ["data is published"],
                "verify_commands": ["node --test tests/data.test.js"],
                "source_refs": ["event:gap"],
            },
            {
                "task_id": "TASK-EVIDENCE-V2",
                "parent_task_id": "TASK-EVIDENCE",
                "claim_paths": ["tests/e2e/**"],
                "acceptance": ["evidence is refreshed"],
                "verify_commands": ["node --test tests/e2e.test.js"],
                "source_refs": ["event:gap"],
            },
        ],
    })

    assert "supersedes_task_ids" not in tasks[0]
    assert tasks[1]["supersedes_task_ids"] == ["TASK-EVIDENCE"]


def test_single_gap_payload_keeps_legacy_aggregate_supersedes() -> None:
    tasks = gap_tasks_from_gap_plan_payload({
        "supersedes_task_ids": ["TASK-OLD"],
        "gap_tasks": [{
            "task_id": "TASK-NEW",
            "claim_paths": ["src/**"],
            "acceptance": ["replacement passes"],
            "verify_commands": ["pytest tests/test_new.py"],
            "source_refs": ["event:gap"],
        }],
    })

    assert tasks[0]["supersedes_task_ids"] == ["TASK-OLD"]


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


def test_semantic_replan_replaces_superseded_task_in_amended_map() -> None:
    base_commit = "a" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "ISSUE-1",
        "source_refs": {"source_index_ref": "docs/plans/source-index.json"},
        "tasks": [{
            "task_id": "ISSUE-1-MIXED",
            "title": "mixed task",
            "owner_role": "dev",
            "wave": 0,
            "allowed_paths": ["src/**"],
            "allowed_paths_reason": "initial mixed slice",
            "acceptance": ["issue is fixed"],
        }],
    }
    replacements = [{
        "task_id": "ISSUE-1-CORE",
        "parent_task_id": "ISSUE-1-MIXED",
        "claim_paths": ["src/core/**", "tests/test_core.py"],
        "acceptance": ["core expiry behavior is fixed"],
        "verify_commands": ["uv run pytest tests/test_core.py"],
        "base_commit": base_commit,
        "source_refs": ["docs/issues/1.md", f"git:{base_commit}"],
        "supersedes_task_ids": ["ISSUE-1-MIXED"],
    }]

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=replacements,
        supersedes_task_map_ref="artifacts/ISSUE-1/task_map.json",
    )

    assert [task["task_id"] for task in amended["tasks"]] == ["ISSUE-1-CORE"]
    assert amended["amend"]["superseded_task_ids"] == ["ISSUE-1-MIXED"]
    assert amended["tasks"][0]["base_commit"] == base_commit
    assert validate_task_map_payload(amended).passed is True


def test_repeated_gap_plan_accepts_recorded_supersede_lineage_idempotently() -> None:
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "ISSUE-1",
        "tasks": [
            {
                "task_id": "WEB-OLD",
                "title": "old web",
                "owner_role": "dev-web",
                "wave": 1,
                "allowed_paths": ["web/src/**"],
                "allowed_paths_reason": "original web slice",
                "acceptance": ["old web works"],
            },
            {
                "task_id": "RELEASE",
                "title": "release",
                "owner_role": "dev-release",
                "wave": 2,
                "blocked_by": ["WEB-OLD"],
                "allowed_paths": ["evidence/**"],
                "allowed_paths_reason": "release evidence",
                "acceptance": ["release closes"],
            },
        ],
    }
    replacement = {
        "task_id": "WEB-NEW",
        "owner_role": "dev-web",
        "base_commit": "a" * 40,
        "claim_paths": ["web/src/**", "tests/web/**"],
        "acceptance": ["new web works"],
        "verify_commands": ["npm --prefix web test"],
        "source_refs": ["reports/web-gap.json"],
        "supersedes_task_ids": ["WEB-OLD"],
    }

    first = build_gap_task_map_amend(
        base,
        gap_tasks=[replacement],
        supersedes_task_map_ref="artifacts/ISSUE-1/task-map-v1.json",
    )
    replayed = build_gap_task_map_amend(
        first,
        gap_tasks=[replacement],
        supersedes_task_map_ref="artifacts/ISSUE-1/task-map-v2.json",
    )

    assert [task["task_id"] for task in replayed["tasks"]] == ["RELEASE", "WEB-NEW"]
    assert replayed["amend"]["gap_task_ids"] == []
    assert replayed["amend"]["superseded_task_ids"] == ["WEB-OLD"]
    assert next(
        task for task in replayed["tasks"] if task["task_id"] == "RELEASE"
    )["blocked_by"] == ["WEB-NEW"]
    assert validate_task_map_payload(replayed).passed is True

def test_repeated_gap_plan_rejects_unproven_historical_predecessor() -> None:
    base = {
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "WEB-NEW",
            "title": "new web",
            "owner_role": "dev-web",
            "wave": 1,
            "allowed_paths": ["web/src/**"],
            "allowed_paths_reason": "new web slice",
            "acceptance": ["new web works"],
        }],
    }
    replacement = {
        "task_id": "WEB-NEW",
        "claim_paths": ["web/src/**"],
        "acceptance": ["new web works"],
        "verify_commands": ["npm --prefix web test"],
        "source_refs": ["reports/web-gap.json"],
        "supersedes_task_ids": ["WEB-OLD"],
    }

    with pytest.raises(ValueError, match="gap plan supersedes unknown task ids: WEB-OLD"):
        build_gap_task_map_amend(
            base,
            gap_tasks=[replacement],
            supersedes_task_map_ref="artifacts/ISSUE-1/task-map-v2.json",
        )

def test_semantic_replan_rewrites_downstream_dependency_to_successor() -> None:
    base_commit = "b" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "PRD-1",
        "tasks": [
            {
                "task_id": "TASK-FOUNDATION",
                "title": "foundation",
                "owner_role": "dev",
                "wave": 1,
                "allowed_paths": ["src/foundation/**"],
                "allowed_paths_reason": "foundation slice",
                "acceptance": ["foundation passes"],
                "validation": {
                    "commands": [{
                        "id": "VC-FOUNDATION-SMOKE",
                        "command": "npm test -- foundation",
                        "acceptance_ids": [],
                        "owner": "task_verify",
                        "tier": "runtime",
                        "deterministic": True,
                        "reusable": True,
                        "timeout_seconds": 180,
                        "rolling_smoke": True,
                        "producer_task_id": "TASK-FOUNDATION",
                    }],
                },
            },
            {
                "task_id": "TASK-INTEGRATION",
                "title": "integration",
                "owner_role": "dev",
                "wave": 2,
                "blocked_by": ["TASK-FOUNDATION"],
                "allowed_paths": ["src/integration/**"],
                "allowed_paths_reason": "integration slice",
                "acceptance": ["integration passes"],
            },
        ],
    }
    replacement = {
        "task_id": "TASK-FOUNDATION-CONTRACT-CLOSURE",
        "parent_task_id": "TASK-PRD-ROOT",
        "owner_role": "dev",
        "claim_paths": ["src/foundation/**"],
        "acceptance": ["focused foundation gate passes"],
        "verify_commands": ["npm test -- foundation"],
        "base_commit": base_commit,
        "source_refs": ["event:verify-blocked", f"git:{base_commit}"],
        "supersedes_task_ids": ["TASK-FOUNDATION"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[replacement],
        supersedes_task_map_ref="artifacts/PRD-1/task_map.json",
    )

    tasks = {task["task_id"]: task for task in amended["tasks"]}
    assert tasks["TASK-FOUNDATION-CONTRACT-CLOSURE"]["wave"] == 1
    assert tasks["TASK-FOUNDATION-CONTRACT-CLOSURE"][
        "parent_task_id"
    ] == "TASK-PRD-ROOT"
    assert tasks["TASK-INTEGRATION"]["blocked_by"] == [
        "TASK-FOUNDATION-CONTRACT-CLOSURE",
    ]
    inherited_command = tasks["TASK-FOUNDATION-CONTRACT-CLOSURE"][
        "validation"
    ]["commands"][0]
    assert inherited_command["id"] == "VC-FOUNDATION-SMOKE"
    assert inherited_command["rolling_smoke"] is True
    assert inherited_command["producer_task_id"] == (
        "TASK-FOUNDATION-CONTRACT-CLOSURE"
    )
    assert validate_task_map_payload(amended).passed is True


@pytest.mark.parametrize("change,expected", [
    ({}, True),
    ({"command": "npm test -- other"}, None),
    ({"rolling_smoke": False}, False),
    ({"timeout_seconds": 300}, None),
])
def test_replacement_inherits_only_unchanged_approved_smoke(change, expected) -> None:
    parent_command = {
        "id": "SMOKE", "command": "npm test -- foundation", "acceptance_ids": [],
        "owner": "task_verify", "tier": "runtime", "deterministic": True,
        "reusable": True, "timeout_seconds": 180, "producer_task_id": "OLD",
        "rolling_smoke": True,
    }
    new_command = {k: v for k, v in parent_command.items() if k != "rolling_smoke"}
    new_command.update({"producer_task_id": "NEW", **change})
    base = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [{
        "task_id": "OLD", "title": "old", "owner_role": "dev", "wave": 1,
        "allowed_paths": ["src/**"], "allowed_paths_reason": "slice",
        "acceptance": ["works"], "validation": {"commands": [parent_command]},
    }]}
    replacement = {"task_id": "NEW", "owner_role": "dev", "claim_paths": ["src/**"],
                   "acceptance": ["works"], "verify_commands": [new_command["command"]],
                   "source_refs": ["event:gap"], "supersedes_task_ids": ["OLD"],
                   "validation": {"commands": [new_command]}}
    amended = build_gap_task_map_amend(base, gap_tasks=[replacement], supersedes_task_map_ref="base.json")
    result = next(t for t in amended["tasks"] if t["task_id"] == "NEW")
    assert result["validation"]["commands"][0].get("rolling_smoke") is expected
    if "rolling_smoke" not in change:
        assert "rolling_smoke" not in new_command


def test_replacement_preserves_shared_validation_catalog_as_read_only() -> None:
    base_commit = "d" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "PRD-VALIDATION",
        "tasks": [
            {
                "task_id": "TASK-EVIDENCE",
                "title": "produce shared evidence",
                "owner_role": "dev",
                "wave": 1,
                "allowed_paths": [
                    "scripts/release/**",
                    "evidence/release/**",
                ],
                "verification_read_paths": ["./node_modules/.bin/tsx"],
                "allowed_paths_reason": "shared evidence producer",
                "acceptance": ["shared evidence exists"],
                "validation": {
                    "commands": [
                        {
                            "id": "VC-RELEASE",
                            "command": "python scripts/release/check.py",
                            "acceptance_ids": ["AC-RELEASE"],
                            "owner": "task_verify",
                            "producer_task_id": "TASK-EVIDENCE",
                        },
                        {
                            "id": "VC-MANUAL",
                            "command": "node scripts/release/manual.mjs",
                            "acceptance_ids": ["AC-MANUAL"],
                            "owner": "human",
                            "producer_task_id": "TASK-EVIDENCE",
                        },
                    ],
                },
            },
            {
                "task_id": "TASK-CONSUMER",
                "title": "consume shared evidence",
                "owner_role": "verify",
                "wave": 2,
                "blocked_by": ["TASK-EVIDENCE"],
                "allowed_paths": ["src/release.ts"],
                "allowed_paths_reason": "release consumer",
                "acceptance": ["manual release evidence remains available"],
                "acceptance_criteria": [{
                    "id": "AC-MANUAL",
                    "statement": "manual release evidence remains available",
                    "verification_command_ids": ["VC-MANUAL"],
                }],
            },
        ],
    }
    replacement = {
        "task_id": "TASK-EVIDENCE-V2",
        "parent_task_id": "PRD-VALIDATION",
        "owner_role": "dev",
        "claim_paths": ["evidence/release/**"],
        "acceptance": ["release evidence regression is closed"],
        "verify_commands": ["python scripts/release/check.py"],
        "base_commit": base_commit,
        "source_refs": ["event:release-gap", f"git:{base_commit}"],
        "supersedes_task_ids": ["TASK-EVIDENCE"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[replacement],
        supersedes_task_map_ref="artifacts/PRD-VALIDATION/task_map.json",
    )

    task = next(
        item for item in amended["tasks"]
        if item["task_id"] == "TASK-EVIDENCE-V2"
    )
    assert [
        command["id"] for command in task["validation"]["commands"]
    ] == ["VC-RELEASE", "VC-MANUAL"]
    assert {
        command["producer_task_id"]
        for command in task["validation"]["commands"]
    } == {"TASK-EVIDENCE-V2"}
    assert task["allowed_paths"] == ["evidence/release/**"]
    assert task["verification_read_paths"] == [
        "./node_modules/.bin/tsx",
        "scripts/release/**",
        "evidence/release/**",
    ]
    assert validate_task_map_payload(amended).passed is True


def test_replacement_keeps_validation_catalog_when_adding_proof_command() -> None:
    base_commit = "e" * 40
    base = {
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "TASK-EVIDENCE",
                "title": "produce evidence",
                "owner_role": "dev",
                "wave": 1,
                "allowed_paths": ["tests/e2e/**", "artifacts/e2e/**"],
                "allowed_paths_reason": "owns release evidence",
                "acceptance": ["release evidence exists"],
                "validation": {
                    "commands": [{
                        "id": "VC-E2E",
                        "command": "node --test tests/e2e/journey.test.js",
                        "owner": "task_verify",
                        "tier": "runtime",
                        "producer_task_id": "TASK-EVIDENCE",
                    }],
                },
            },
            {
                "task_id": "TASK-CONSUMER",
                "title": "consume evidence",
                "owner_role": "verify",
                "wave": 2,
                "blocked_by": ["TASK-EVIDENCE"],
                "allowed_paths": ["src/release.ts"],
                "allowed_paths_reason": "consumes release evidence",
                "acceptance": ["shared E2E evidence remains addressable"],
                "acceptance_criteria": [{
                    "id": "AC-E2E",
                    "statement": "shared E2E evidence remains addressable",
                    "verification_command_ids": ["VC-E2E"],
                }],
            },
        ],
    }
    replacement = {
        "task_id": "TASK-EVIDENCE-V2",
        "claim_paths": ["tests/e2e/**", "artifacts/e2e/**"],
        "acceptance": ["release evidence gap is closed"],
        "verify_commands": [
            "node --test tests/e2e/journey.test.js",
            f"git diff --name-only {base_commit}..HEAD -- tests/e2e | rg .",
        ],
        "base_commit": base_commit,
        "source_refs": ["event:gap", f"git:{base_commit}"],
        "supersedes_task_ids": ["TASK-EVIDENCE"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[replacement],
        supersedes_task_map_ref="artifacts/task-map.json",
    )

    successor = next(
        task for task in amended["tasks"]
        if task["task_id"] == "TASK-EVIDENCE-V2"
    )
    assert successor["validation"]["commands"][0]["id"] == "VC-E2E"
    assert successor["validation"]["commands"][0]["producer_task_id"] == (
        "TASK-EVIDENCE-V2"
    )
    assert validate_task_map_payload(amended).passed is True


def test_split_replacement_expands_downstream_dependency_to_all_successors() -> None:
    base_commit = "c" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "ISSUE-2",
        "tasks": [
            {
                "task_id": "TASK-MIXED",
                "title": "mixed",
                "owner_role": "dev",
                "wave": 1,
                "allowed_paths": ["src/**"],
                "allowed_paths_reason": "mixed slice",
                "acceptance": ["mixed behavior passes"],
            },
            {
                "task_id": "TASK-ASSEMBLY",
                "title": "assembly",
                "owner_role": "dev",
                "wave": 2,
                "blocked_by": ["TASK-MIXED"],
                "allowed_paths": ["src/app.py"],
                "allowed_paths_reason": "assembly slice",
                "acceptance": ["assembly passes"],
            },
        ],
    }
    replacements = [
        {
            "task_id": task_id,
            "claim_paths": [path, test_path],
            "acceptance": [f"{task_id} passes"],
            "verify_commands": [command],
            "base_commit": base_commit,
            "source_refs": ["event:split", f"git:{base_commit}"],
            "supersedes_task_ids": ["TASK-MIXED"],
        }
        for task_id, path, test_path, command in (
            (
                "TASK-CORE",
                "src/core/**",
                "tests/test_core.py",
                "pytest tests/test_core.py",
            ),
            (
                "TASK-API",
                "src/api/**",
                "tests/test_api.py",
                "pytest tests/test_api.py",
            ),
        )
    ]

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=replacements,
        supersedes_task_map_ref="artifacts/ISSUE-2/task_map.json",
    )

    tasks = {task["task_id"]: task for task in amended["tasks"]}
    assert tasks["TASK-CORE"]["wave"] == 1
    assert tasks["TASK-API"]["wave"] == 1
    assert tasks["TASK-ASSEMBLY"]["blocked_by"] == [
        "TASK-CORE",
        "TASK-API",
    ]
    assert validate_task_map_payload(amended).passed is True


def test_gap_amend_preserves_read_only_verification_scope() -> None:
    base_commit = "c" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "RELEASE",
        "tasks": [{
            "task_id": "RELEASE-EVIDENCE-R10",
            "title": "release evidence",
            "owner_role": "dev",
            "wave": 1,
            "allowed_paths": ["evidence/release/**"],
            "allowed_paths_reason": "release evidence owner",
            "acceptance": ["release evidence exists"],
        }],
    }
    replacement = {
        "task_id": "RELEASE-EVIDENCE-R11",
        "parent_task_id": "RELEASE-EVIDENCE-R10",
        "claim_paths": ["evidence/release/**"],
        "verification_read_paths": [
            "scripts/release/release_gate.py",
            "tests/release/test_release_contract.py",
        ],
        "acceptance": ["release port evidence is current"],
        "verify_commands": [
            "python scripts/release/release_gate.py --check",
            "python -m pytest tests/release/test_release_contract.py -q",
        ],
        "base_commit": base_commit,
        "source_refs": ["reports/release-gap.json", f"git:{base_commit}"],
        "supersedes_task_ids": ["RELEASE-EVIDENCE-R10"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[replacement],
        supersedes_task_map_ref="artifacts/RELEASE/task_map.json",
    )

    task = amended["tasks"][0]
    assert task["allowed_paths"] == ["evidence/release/**"]
    assert task["verification_read_paths"] == [
        "scripts/release/release_gate.py",
        "tests/release/test_release_contract.py",
    ]
    assert validate_task_map_payload(amended).passed is True


def test_assembly_replacement_inherits_parent_mechanical_owner_class() -> None:
    base_commit = "b" * 40
    base = {
        "schema_version": "task-map.v1",
        "feature_id": "PRD-1",
        "tasks": [
            {
                "task_id": "PRD-1-SLICE",
                "title": "slice",
                "owner_role": "dev-lane-0",
                "wave": 1,
                "allowed_paths": ["src/core/**"],
                "allowed_paths_reason": "slice owner",
                "acceptance": ["slice works"],
            },
            {
                "task_id": "PRD-1-ASSEMBLY",
                "title": "assembly",
                "owner_role": "dev-lane-1",
                "affinity_tag": "assembly",
                "root_owner_class": "assembly",
                "wave": 2,
                "allowed_paths": ["src/app.py"],
                "allowed_paths_reason": "assembly owner",
                "acceptance": ["product is assembled"],
            },
        ],
    }
    gap_task = {
        "task_id": "PRD-1-ASSEMBLY-GAP",
        "parent_task_id": "PRD-1-ASSEMBLY",
        "supersedes_task_ids": ["PRD-1-ASSEMBLY"],
        "claim_paths": ["src/app.py", "tests/release/**"],
        "acceptance": ["release assembly closes"],
        "verify_commands": ["python -m pytest tests/release"],
        "base_commit": base_commit,
        "source_refs": ["reports/prd-1-gap.json", f"git:{base_commit}"],
    }

    amended = build_gap_task_map_amend(
        base,
        gap_tasks=[gap_task],
        supersedes_task_map_ref="artifacts/PRD-1/task_map.json",
    )

    replacement = next(
        task for task in amended["tasks"]
        if task["task_id"] == "PRD-1-ASSEMBLY-GAP"
    )
    assert replacement["root_owner_class"] == "assembly"
    assert replacement["affinity_tag"] == "assembly"
    assert replacement["base_commit"] == base_commit
    assert validate_task_map_payload(amended).passed is True


def test_parent_gap_identity_overrides_conflicting_child_goal_identity() -> None:
    tasks = gap_tasks_from_gap_plan_payload({
        "goal_id": "PRD-1",
        "goal_kind": "prd",
        "gap_category": "product_completeness",
        "gap_tasks": [{
            "task_id": "PRD-1-GAP",
            "goal_id": "workflow-run-id",
            "claim_paths": ["src/app.py"],
            "acceptance": ["product closes"],
            "verify_commands": ["python -m pytest"],
            "source_refs": ["reports/prd-1-gap.json"],
            "evidence_contract": {"goal_id": "workflow-run-id"},
        }],
    })

    assert tasks[0]["goal_id"] == "PRD-1"
    amended = build_gap_task_map_amend(
        {
            "schema_version": "task-map.v1",
            "feature_id": "PRD-1",
            "tasks": [],
        },
        gap_tasks=tasks,
        supersedes_task_map_ref="artifacts/PRD-1/task_map.json",
    )
    assert amended["tasks"][0]["evidence_contract"]["goal_id"] == "PRD-1"
