from __future__ import annotations

import pytest

from zf.runtime.task_map import (
    validate_source_index_payload,
    validate_task_map_payload,
)


def test_task_map_validation_accepts_dependency_layers() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "TASK-A",
                "title": "A",
                "wave": 1,
                "verification": "pytest tests/a.py",
                "exclusive_files": ["src/a.py"],
            },
            {
                "task_id": "TASK-B",
                "title": "B",
                "blocked_by": ["TASK-A"],
                "wave": 2,
                "acceptance": ["pytest tests/b.py"],
                "exclusive_files": ["src/b.py"],
            },
        ],
    })

    assert result.passed is True
    assert result.summary["task_count"] == 2
    assert result.summary["wave_count"] == 2


def test_task_map_validation_accepts_simple_issue_serial_task() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "ISSUE-CALC-ADD",
                "title": "Fix add regression",
                "owner_role": "dev-core",
                "wave": 1,
                "allowed_paths": ["src/calc.py", "tests/test_calc.py"],
                "verification": "uv run pytest tests/test_calc.py -q",
            },
        ],
    })

    assert result.passed is True
    assert result.summary["task_count"] == 1


def test_task_map_quality_contract_requires_inventory_binding_for_blocking_tasks() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "quality_contract": {
            "require_inventory_binding": True,
            "blocking_priorities": ["P0", "P1"],
            "require_non_smoke_for_blocking_inventory": True,
        },
        "tasks": [
            {
                "task_id": "CANGJIE-WEB-001",
                "title": "Implement WebChat",
                "priority": "P0",
                "wave": 1,
                "allowed_paths": ["packages/web/**", "packages/web-server/**"],
                "allowed_paths_reason": "Owns WebChat product surface.",
                "verification": "pnpm test",
                "source_refs": ["docs/plans/hermes-web-dashboard-inventory.json#WEBCHAT"],
            },
        ],
    })

    assert result.passed is False
    assert any("inventory_ids" in error for error in result.errors)
    assert any("non_smoke_test_required" in error for error in result.errors)


def test_task_map_quality_contract_accepts_inventory_bound_blocking_task() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "quality_contract": {
            "require_inventory_binding": True,
            "blocking_priorities": ["P0", "P1"],
            "require_non_smoke_for_blocking_inventory": True,
        },
        "tasks": [
            {
                "task_id": "CANGJIE-WEB-001",
                "title": "Implement WebChat",
                "priority": "P0",
                "wave": 1,
                "allowed_paths": ["packages/web/**", "packages/web-server/**"],
                "allowed_paths_reason": "Owns WebChat product surface.",
                "verification": "pnpm test",
                "source_refs": ["docs/plans/hermes-web-dashboard-inventory.json#WEBCHAT"],
                "inventory_ids": ["WEBCHAT"],
                "non_smoke_test_required": True,
            },
        ],
    })

    assert result.passed is True, result.errors


def test_task_map_validation_accepts_pytest_node_id_verification() -> None:
    # A pytest node-id (path::test_name) targets a node in an in-scope file;
    # it must not be rejected as a path outside allowed_paths.
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "ISSUE-MEDIAN",
                "title": "Fix median even-length",
                "owner_role": "dev-core",
                "wave": 1,
                "allowed_paths": ["src/statkit/stats.py", "tests/test_stats.py"],
                "exclusive_files": ["src/statkit/stats.py"],
                "verification": "python3 -m pytest tests/test_stats.py::test_median_even -q",
            },
        ],
    })

    assert result.passed is True, result.errors
    assert not any("references path outside" in e for e in result.errors)


def test_task_map_validation_allows_cross_task_verification() -> None:
    # Refactor characterization pattern: the refactor task verifies against a
    # test file owned by a sibling characterization task in the same plan.
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "RG-CHAR-001",
                "owner_role": "dev-core",
                "wave": 1,
                "allowed_paths": ["tests/test_characterization.py"],
                "verification": "python3 -m pytest tests/test_characterization.py -q",
            },
            {
                "task_id": "RG-REFAC-002",
                "owner_role": "dev-core",
                "wave": 2,
                "allowed_paths": ["src/reportgen/report.py", "tests/test_report.py"],
                "exclusive_files": ["src/reportgen/report.py"],
                "verification": (
                    "python3 -m pytest tests/test_characterization.py "
                    "tests/test_report.py -q"
                ),
            },
        ],
    })

    assert result.passed is True, result.errors
    assert not any("references path outside" in e for e in result.errors)


def test_task_map_validation_rejects_out_of_scope_node_id_file() -> None:
    # The file part of an out-of-scope node-id is still caught.
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "ISSUE-MEDIAN",
                "title": "Fix median",
                "owner_role": "dev-core",
                "wave": 1,
                "allowed_paths": ["src/statkit/stats.py", "tests/test_stats.py"],
                "exclusive_files": ["src/statkit/stats.py"],
                "verification": "python3 -m pytest tests/test_other.py::test_x -q",
            },
        ],
    })

    assert result.passed is False
    assert any("references path outside" in e for e in result.errors)


def test_task_map_validation_rejects_unknown_dependency_and_file_overlap() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "TASK-A",
                "blocked_by": ["TASK-MISSING"],
                "wave": 1,
                "verification": "pytest tests/a.py",
                "exclusive_files": ["src/shared.py"],
            },
            {
                "task_id": "TASK-B",
                "wave": 1,
                "verification": "pytest tests/b.py",
                "exclusive_files": ["src/shared.py"],
            },
        ],
    })

    assert result.passed is False
    assert any("unknown task" in error for error in result.errors)
    assert any("exclusive_files overlap" in error for error in result.errors)


def test_task_map_validation_rejects_prose_tail_in_verification_command() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "TASK-BUG-A",
                "title": "fix log counter",
                "wave": 1,
                "verification": (
                    "uv run pytest tests/e2e/test_log_counter.py -q "
                    "(red before TASK-BUG-B)"
                ),
            },
        ],
    })

    assert result.passed is False
    assert any("verification must be valid shell syntax" in error for error in result.errors)


def test_task_map_validation_rejects_lossy_single_quoted_bash_payload() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "CJMIN-ASSEMBLY-001",
                "title": "assembly",
                "wave": 1,
                "verification": (
                    "bash -lc 'set -euo pipefail; "
                    "pnpm --filter @cj-min/contracts exec node -e "
                    "\"const fs=require('node:fs');"
                    "JSON.parse(fs.readFileSync('package.json','utf8'))\"'"
                ),
            },
        ],
    })

    assert result.passed is False
    assert any("must not wrap bash -c payload in single quotes" in error for error in result.errors)


def test_task_map_validation_rejects_unquoted_pnpm_glob_filter() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "CJMIN-ASSEMBLY-001",
                "title": "assembly",
                "wave": 1,
                "verification": "pnpm --filter ./packages/** run typecheck",
            },
        ],
    })

    assert result.passed is False
    assert any("must quote shell glob filter arguments" in error for error in result.errors)


def test_task_map_validation_accepts_quoted_pnpm_glob_filter() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "CJMIN-ASSEMBLY-001",
                "title": "assembly",
                "wave": 1,
                "verification": 'pnpm --filter "./packages/**" run typecheck',
            },
        ],
    })

    assert result.passed is True


def test_task_map_validation_rejects_unquoted_path_glob() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "CJMIN-ASSEMBLY-001",
                "title": "assembly",
                "wave": 1,
                "verification": "test -d ./packages/**",
            },
        ],
    })

    assert result.passed is False
    assert any("must quote shell glob path arguments" in error for error in result.errors)


def test_task_map_validation_rejects_verification_path_outside_allowed_paths() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "CJMIN-PACKAGING-DOCKER-SECURITY-001",
                "title": "security package",
                "wave": 1,
                "allowed_paths": ["packages/security/src"],
                "verification": "pnpm exec vitest packages/security/test/security.test.ts",
            },
        ],
    })

    assert result.passed is False
    assert result.summary["tasks_missing_allowed_paths_reason"] == [
        "CJMIN-PACKAGING-DOCKER-SECURITY-001",
    ]
    assert any("references path outside allowed_paths" in error for error in result.errors)


def test_task_map_validation_accepts_verification_path_inside_allowed_paths() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "CJMIN-PACKAGING-DOCKER-SECURITY-001",
                "title": "security package",
                "wave": 1,
                "allowed_paths": ["packages/security"],
                "allowed_paths_reason": "security package owns src and test fixtures",
                "verification": "pnpm exec vitest packages/security/test/security.test.ts",
            },
        ],
    })

    assert result.passed is True
    assert result.summary["tasks_missing_allowed_paths_reason"] == []


def test_task_map_validation_accepts_package_root_relative_node_commands() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "target_root": "app",
        "shared_conventions": {
            "package_root": "app",
            "test_path_prefix": "app/test/",
            "run_cwd": "app (commands run from the package root)",
        },
        "waves": [
            {"wave": 1, "tasks": ["TASK-SCAFFOLD"]},
            {"wave": 2, "tasks": ["TASK-CLI"]},
        ],
        "tasks": [
            {
                "task_id": "TASK-SCAFFOLD",
                "title": "scaffold",
                "wave": 1,
                "allowed_paths": [
                    "app/package.json",
                    "app/src/.gitkeep",
                    "app/test/.gitkeep",
                ],
                "verification": [
                    (
                        "cd app && node -e \"const p=require('./package.json'); "
                        "if(p.scripts.test!=='node --test test/'){process.exit(1)}\""
                    ),
                    "test -d app/src && test -d app/test",
                ],
            },
            {
                "task_id": "TASK-CLI",
                "title": "cli",
                "wave": 2,
                "allowed_paths": [
                    "app/src/index.js",
                    "app/test/cli.test.js",
                ],
                "blocked_by": ["TASK-SCAFFOLD"],
                "verification": [
                    "cd app && test \"$(node src/index.js echo hello)\" = \"hello\"",
                    "cd app && node src/index.js help | grep -qF 'echo <text>'",
                    "cd app && node --test test/",
                ],
            },
        ],
    })

    assert result.passed is True, result.errors
    assert result.summary["wave_count"] == 2
    assert result.summary["tasks_by_wave"] == {"1": 1, "2": 1}


def test_task_map_validation_rejects_package_root_parent_escape() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "target_root": "app",
        "shared_conventions": {"package_root": "app"},
        "tasks": [
            {
                "task_id": "TASK-CLI",
                "title": "cli",
                "wave": 1,
                "allowed_paths": ["app/src/index.js"],
                "verification": "cd app && node ../outside.js",
            },
        ],
    })

    assert result.passed is False
    assert any("references path outside allowed_paths" in error for error in result.errors)


def test_task_map_validation_ignores_sentence_punctuation_on_path_refs() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "PPULSE-CORE-001",
                "title": "Build Product Pulse HTTP vertical slice",
                "wave": 1,
                "allowed_paths": [
                    "src/server.mjs",
                    "tests/server.test.mjs",
                    "package.json",
                ],
                "allowed_paths_reason": "single runtime slice owns service and tests",
                "acceptance": [
                    "Update src/server.mjs and tests/server.test.mjs.",
                    "Keep package.json dependency-free.",
                ],
                "verification": "npm test",
            },
        ],
    })

    assert result.passed is True, result.errors
    assert not any("references path outside" in error for error in result.errors)


def test_task_map_validation_rejects_prose_in_structured_validation_command() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "TASK-RED-A",
                "title": "expected red",
                "wave": 1,
                "validation": {
                    "kind": "command",
                    "command": "false；这是 expected red 证据",
                    "expected_result": "red",
                },
            },
        ],
    })

    assert result.passed is False
    assert any("validation.command must be an executable command only" in error for error in result.errors)


def test_task_map_validation_can_accept_structural_candidate_map() -> None:
    result = validate_task_map_payload(
        {
            "schema_version": "task-map.v1",
            "tasks": [
                {"task_id": "TASK-A", "wave": 1},
                {"task_id": "TASK-B", "blocked_by": ["TASK-A"], "wave": 2},
            ],
        },
        require_task_verification=False,
    )

    assert result.passed is True


def test_source_index_validation_requires_task_map_coverage() -> None:
    task_map = {
        "schema_version": "task-map.v1",
        "tasks": [
            {"task_id": "TASK-A", "verification": "pytest", "wave": 1},
            {"task_id": "TASK-B", "verification": "pytest", "wave": 1},
        ],
    }

    result = validate_source_index_payload(
        {
            "schema_version": "source-index.v1",
            "tasks": [{
                "task_id": "TASK-A",
                "source_key": "plan.md#task-a",
                "source_ref": "plan.md#task-a",
                "source_excerpt": "Task A from the accepted plan.",
            }],
        },
        task_map=task_map,
    )

    assert result.passed is False
    assert "TASK-B" in result.summary["missing_task_ids"]
    assert any("source_index missing task_id 'TASK-B'" in error for error in result.errors)


def test_source_index_validation_allows_explicit_degraded_source_off_main_path() -> None:
    result = validate_source_index_payload(
        {
            "schema_version": "source-index.v1",
            "tasks": [{
                "task_id": "TASK-A",
                "source_key": "legacy:TASK-A",
                "source_ref": "legacy:TASK-A",
                "source_excerpt": "Legacy behavior summary.",
                "source_mode": "degraded",
                "degraded_reason": "legacy task without canonical bundle",
            }],
        },
        require_canonical=False,
    )

    assert result.passed is True
    assert result.summary["source_modes"]["degraded"] == 1


def _task(task_id, owner_role, *, root_owner_class="", wave=1, blocked_by=None):
    return {
        "task_id": task_id,
        "title": task_id,
        "owner_role": owner_role,
        "root_owner_class": root_owner_class,
        "wave": wave,
        "blocked_by": blocked_by or [],
        "verification": f"pytest tests/{task_id.lower()}.py",
        "exclusive_files": [f"src/{task_id.lower()}.py"],
    }


def test_task_map_validation_requires_assembly_task_for_parallel_bundles() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            _task("AVBS-SCENE-001", "dev-scene"),
            _task("AVBS-FLOW-001", "dev-flow"),
            _task("AVBS-METRICS-001", "dev-metrics"),
        ],
    })

    assert result.passed is False
    assert any("缺 assembly 任务" in error for error in result.errors)


def test_task_map_validation_rejects_assembly_owner_colliding_with_bundle() -> None:
    # Reproduces the avbs-r1 self-deadlock: assembly task glued onto the same
    # owner_role as one of the parallel bundles it depends on.
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            _task("AVBS-SCENE-001", "dev-scene"),
            _task("AVBS-FLOW-001", "dev-flow"),
            _task("AVBS-METRICS-001", "dev-metrics"),
            _task(
                "AVBS-ASSEMBLY-001", "dev-flow",
                root_owner_class="assembly", wave=2,
                blocked_by=["AVBS-SCENE-001", "AVBS-FLOW-001", "AVBS-METRICS-001"],
            ),
        ],
    })

    assert result.passed is False
    assert any("自锁" in error for error in result.errors)


def test_task_map_validation_accepts_assembly_task_with_independent_owner() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            _task("AVBS-SCENE-001", "dev-scene"),
            _task("AVBS-FLOW-001", "dev-flow"),
            _task("AVBS-METRICS-001", "dev-metrics"),
            _task(
                "AVBS-ASSEMBLY-001", "dev-assembly",
                root_owner_class="assembly", wave=2,
                blocked_by=["AVBS-SCENE-001", "AVBS-FLOW-001", "AVBS-METRICS-001"],
            ),
        ],
    })

    assert result.passed is True
    assert result.summary["bundle_owner_count"] == 3
    assert result.summary["assembly_task_count"] == 1


def test_task_map_validation_allows_single_owner_serial_plan_without_assembly() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            _task("TASK-A", "dev-core", wave=1),
            _task("TASK-B", "dev-core", wave=2, blocked_by=["TASK-A"]),
        ],
    })

    assert result.passed is True


@pytest.mark.parametrize(
    ("task_map", "field"),
    [
        (
            {"workspace_root_owner_required": "true"},
            "workspace_root_owner_required",
        ),
        (
            {"refactor_contract": {"workspace_root_owner_required": 1}},
            "refactor_contract.workspace_root_owner_required",
        ),
    ],
)
def test_task_map_validation_requires_boolean_root_owner_requirement(
    task_map: dict,
    field: str,
) -> None:
    payload = {
        "schema_version": "task-map.v1",
        "tasks": [_task("TASK-A", "dev-core")],
        **task_map,
    }

    result = validate_task_map_payload(payload)

    assert result.passed is False
    assert any(f"{field} must be a boolean" in error for error in result.errors)
