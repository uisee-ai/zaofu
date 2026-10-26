from __future__ import annotations

from pathlib import Path

import pytest

from zf.runtime.task_map import (
    resolve_artifact_file,
    validate_source_index_payload,
    validate_task_map_payload,
)
from zf.runtime.verification_path_refs import command_path_refs


def test_resolve_artifact_file_maps_dot_zf_to_configured_state_dir(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = tmp_path / ".zf-custom"
    artifact = state_dir / "artifacts" / "GOAL-1" / "task_map.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"tasks": []}\n', encoding="utf-8")

    resolved = resolve_artifact_file(
        ".zf/artifacts/GOAL-1/task_map.json",
        project_root=project_root,
        state_dir=state_dir,
    )

    assert resolved == artifact


def test_resolve_artifact_file_uses_unique_worker_copy_as_legacy_fallback(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = tmp_path / ".zf-custom"
    artifact = (
        state_dir / "workdirs" / "planner-1" / "project"
        / ".zf" / "artifacts" / "GOAL-1" / "task_map.json"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"tasks": []}\n', encoding="utf-8")

    resolved = resolve_artifact_file(
        ".zf/artifacts/GOAL-1/task_map.json",
        project_root=project_root,
        state_dir=state_dir,
    )

    assert resolved == artifact


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


def test_task_map_goal_coverage_reports_uncovered_claim_without_blocking() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "goal_claims": [
            {"goal_claim_id": "CLAIM-A", "text": "A", "mandatory": True},
            {"goal_claim_id": "CLAIM-B", "text": "B", "mandatory": True},
        ],
        "tasks": [
            {
                "task_id": "TASK-A",
                "goal_claim_ids": ["CLAIM-A"],
                "verification": "pytest tests/a.py",
            },
        ],
    })

    assert result.passed is True
    assert result.summary["goal_coverage"] == {
        "mode": "explicit",
        "claim_count": 2,
        "mapped_claim_count": 1,
        "diagnostics": [{
            "code": "mandatory_claim_uncovered",
            "goal_claim_id": "CLAIM-B",
        }],
    }


def test_task_map_goal_coverage_rejects_unknown_and_duplicate_claim_refs() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "goal_claims": [
            {"goal_claim_id": "CLAIM-A", "text": "A", "mandatory": True},
        ],
        "tasks": [
            {
                "task_id": "TASK-A",
                "goal_claim_ids": ["CLAIM-A", "CLAIM-A", "CLAIM-MISSING"],
                "verification": "pytest tests/a.py",
            },
        ],
    })

    assert result.passed is False
    assert "duplicate_goal_claim_id task=TASK-A goal_claim_id=CLAIM-A" in result.errors
    assert "unknown_goal_claim_id task=TASK-A goal_claim_id=CLAIM-MISSING" in result.errors


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


def test_task_map_validation_rejects_file_overlap_even_when_tasks_are_serialized() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "TASK-A",
                "wave": 1,
                "verification": "pytest tests/a.py",
                "exclusive_files": ["src/shared.py"],
            },
            {
                "task_id": "TASK-B",
                "blocked_by": ["TASK-A"],
                "wave": 2,
                "verification": "pytest tests/b.py",
                "exclusive_files": ["src/shared.py"],
            },
        ],
    })

    assert result.passed is False
    assert result.errors == [
        "exclusive_files overlap 'src/shared.py': TASK-A and TASK-B",
    ]


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


def test_task_map_validation_rejects_escape_sensitive_inline_eval() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "REFACTOR-NAME-001",
                "title": "extract helper",
                "wave": 1,
                "verification": (
                    "node -e \"const re=new RegExp("
                    "'export\\\\s+function'); if(!re) process.exit(1)\""
                ),
            },
        ],
    })

    assert result.passed is False
    assert any(
        "must not embed escape-sensitive code in node -e/python -c" in error
        for error in result.errors
    )


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


def test_task_map_validation_accepts_unquoted_case_path_pattern() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "BROWSER-SNAPSHOT-001",
                "title": "browser snapshot",
                "wave": 1,
                "verification": (
                    'snapshot="/tmp/warehouse-route-e2e.abc123"; '
                    'case "$snapshot" in '
                    '/tmp/warehouse-route-e2e.*) ;; *) exit 1 ;; esac'
                ),
            },
        ],
    })

    assert result.passed is True


def test_task_map_validation_rejects_unquoted_path_glob_in_case_body() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "BROWSER-SNAPSHOT-001",
                "title": "browser snapshot",
                "wave": 1,
                "verification": (
                    'snapshot="/tmp/warehouse-route-e2e.abc123"; '
                    'case "$snapshot" in '
                    '/tmp/warehouse-route-e2e.*) test -d ./packages/** ;; '
                    '*) exit 1 ;; esac'
                ),
            },
        ],
    })

    assert result.passed is False
    assert any(
        "./packages/**" in error
        for error in result.errors
        if "must quote shell glob path arguments" in error
    )


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


def test_task_map_validation_accepts_explicit_verification_read_paths() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "RELEASE-EVIDENCE-R11",
                "title": "Refresh release evidence",
                "wave": 1,
                "allowed_paths": ["evidence/release/**"],
                "allowed_paths_reason": "Owns generated release evidence only.",
                "verification_read_paths": [
                    "scripts/release/release_gate.py",
                    "tests/release/test_plan_admission_successor.py",
                    "tests/release/test_release_contract.py",
                ],
                "verification": [
                    "python scripts/release/release_gate.py --check",
                    (
                        "python -m pytest "
                        "tests/release/test_plan_admission_successor.py "
                        "tests/release/test_release_contract.py -q"
                    ),
                ],
            },
        ],
    })

    assert result.passed is True, result.errors


def test_task_map_verification_read_paths_do_not_allow_unlisted_refs() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "RELEASE-EVIDENCE-R11",
                "title": "Refresh release evidence",
                "wave": 1,
                "allowed_paths": ["evidence/release/**"],
                "allowed_paths_reason": "Owns generated release evidence only.",
                "verification_read_paths": ["scripts/release/release_gate.py"],
                "verification": (
                    "python -m pytest tests/release/test_unlisted_contract.py -q"
                ),
            },
        ],
    })

    assert result.passed is False
    assert any(
        "references path outside allowed_paths" in error
        and "test_unlisted_contract.py" in error
        for error in result.errors
    )


def test_task_map_ignores_external_executable_and_runtime_state_dir() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "RELEASE-EVIDENCE-R12",
                "title": "Refresh release evidence",
                "wave": 1,
                "allowed_paths": ["evidence/release/**"],
                "allowed_paths_reason": "Owns generated release evidence only.",
                "verification_read_paths": [
                    "scripts/release/release_gate.py",
                    "tests/release/test_release_contract.py",
                ],
                "verification": [
                    (
                        "/opt/zaofu/bin/zf events --state-dir "
                        "/tmp/project/.zf-runtime trace evt-authority"
                    ),
                    (
                        "uv run python -m pytest "
                        "tests/release/test_release_contract.py -q"
                    ),
                    "python scripts/release/release_gate.py --check",
                ],
            },
        ],
    })

    assert result.passed is True, result.errors


def test_external_executable_does_not_hide_other_absolute_path_arguments() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "RELEASE-EVIDENCE-R12",
                "title": "Refresh release evidence",
                "wave": 1,
                "allowed_paths": ["evidence/release/**"],
                "allowed_paths_reason": "Owns generated release evidence only.",
                "verification": "/bin/cat /tmp/unowned-release-evidence.json",
            },
        ],
    })

    assert result.passed is False
    assert any(
        "references path outside allowed_paths" in error
        and "/tmp/unowned-release-evidence.json" in error
        for error in result.errors
    )


def test_pinned_oci_image_ref_is_not_treated_as_repository_path() -> None:
    digest = "6cfb1e2f2ba7ace93882e692df6a4b68825bdbfb40d649b39878157acfd2d6d8"
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            {
                "task_id": "BROWSER-E2E-RUNNER",
                "title": "Run browser acceptance",
                "wave": 1,
                "allowed_paths": ["artifacts/e2e/**"],
                "allowed_paths_reason": "Owns browser evidence only.",
                "verification": (
                    "docker run --rm --pull=never "
                    f"mcp/playwright@sha256:{digest}"
                ),
            },
        ],
    })

    assert result.passed is True, result.errors


def test_docker_playwright_runtime_flags_are_not_task_paths() -> None:
    command = (
        'docker run --rm --network host --mount "type=bind,src=$PWD,dst=/work" '
        '--mount "type=bind,src=/path/to/zaofu/web/node_modules,'
        'dst=/opt/zaofu-node_modules,readonly" --workdir /work '
        '--env NODE_PATH=/opt/zaofu-node_modules mcp/playwright:latest '
        'node --test verification/browser-acceptance.mjs'
    )
    assert command_path_refs(command) == ["verification/browser-acceptance.mjs"]


@pytest.mark.parametrize("prefix", [".", "./", "././"])
def test_root_test_convention_accepts_project_relative_test_files(prefix):
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "shared_conventions": {"test_path_prefix": prefix},
        "tasks": [{"task_id": "regression", "title": "Fix counter",
                   "allowed_paths": ["counter.py", "test_counter.py"],
                   "verification": "python -m unittest test_counter.py"}],
    })
    assert result.passed, result.errors


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


def test_task_map_validation_treats_node_modules_bin_as_dependency_tool() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-WEB-VERIFY",
            "title": "verify web behavior",
            "allowed_paths": ["tests/web/**"],
            "allowed_paths_reason": "owns focused web tests",
            "acceptance": ["web behavior passes"],
            "verification": (
                "./node_modules/.bin/tsx --test tests/web/behavior.test.mts"
            ),
        }],
    })

    assert result.passed is True, result.errors


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


def test_task_map_rejects_external_acceptance_evidence_without_dependency() -> None:
    result = validate_task_map_payload(
        {
            "schema_version": "task-map.v1",
            "tasks": [
                {
                    "task_id": "TASK-IMPL",
                    "owner_role": "impl",
                    "allowed_paths": ["artifacts/impl.json"],
                    "wave": 1,
                    "verification": "test -f artifacts/impl.json",
                },
                {
                    "task_id": "TASK-VERIFY",
                    "owner_role": "verify",
                    "wave": 1,
                    "acceptance_criteria": [{
                        "id": "AC-EXTERNAL",
                        "statement": "The implementation evidence is accepted.",
                        "evidence_requirements": [{
                            "producer_task_id": "TASK-IMPL",
                            "producer_paths": ["artifacts/impl.json"],
                            "independence_required": True,
                        }],
                    }],
                    "verification": "test -f artifacts/impl.json",
                },
            ],
        },
    )

    assert result.passed is False
    assert any("does not depend on evidence producer" in error for error in result.errors)


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


def test_task_map_validation_allows_later_gap_to_reuse_assembly_owner() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            _task("AVBS-SCENE-001", "dev-scene"),
            _task("AVBS-FLOW-001", "dev-flow"),
            _task(
                "AVBS-ASSEMBLY-001", "dev-assembly",
                root_owner_class="assembly", wave=2,
                blocked_by=["AVBS-SCENE-001", "AVBS-FLOW-001"],
            ),
            _task(
                "AVBS-GAP-001", "dev-assembly", wave=3,
                blocked_by=["AVBS-ASSEMBLY-001"],
            ),
        ],
    })

    assert result.passed is True


def test_task_map_validation_allows_single_owner_serial_plan_without_assembly() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            _task("TASK-A", "dev-core", wave=1),
            _task("TASK-B", "dev-core", wave=2, blocked_by=["TASK-A"]),
        ],
    })

    assert result.passed is True


def test_task_map_validation_allows_distinct_owner_serial_plan_without_assembly() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [
            _task("TASK-SCAFFOLD", "dev-runtime", wave=1),
            _task(
                "TASK-HTTP",
                "dev-web",
                wave=2,
                blocked_by=["TASK-SCAFFOLD"],
            ),
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


@pytest.mark.parametrize(
    "required_plan_ports",
    [
        "acceptance_matrix",
        [],
        ["prd_ref", "requirement_spec"],
        ["acceptance/matrix"],
    ],
)
def test_task_map_validation_rejects_invalid_required_plan_ports(
    required_plan_ports: object,
) -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "required_plan_ports": required_plan_ports,
        "tasks": [_task("TASK-A", "dev-core")],
    })

    assert result.passed is False
    assert any("required_plan_ports" in error for error in result.errors)


def test_task_map_validation_accepts_contract_verification_owner_alias() -> None:
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-CONTRACT",
            "verification": "git diff --check",
            "acceptance_criteria": [{
                "id": "AC-SCOPE",
                "statement": "The change remains within the declared scope.",
                "verification_owner": "contract",
                "verification_tier": "manual_evidence",
            }],
        }],
    })

    assert result.passed is True, result.errors


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verification_owner", "mystery"),
        ("verification_tier", "mystery"),
    ],
)
def test_task_map_validation_rejects_unroutable_acceptance_contract(
    field: str,
    value: str,
) -> None:
    criterion = {
        "id": "AC-SCOPE",
        "statement": "The change remains within the declared scope.",
        "verification_owner": "task_verify",
        "verification_tier": "manual_evidence",
        field: value,
    }
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-CONTRACT",
            "verification": "git diff --check",
            "acceptance_criteria": [criterion],
        }],
    })

    assert result.passed is False
    assert any(field in error and "unsupported" in error for error in result.errors)


@pytest.mark.parametrize(
    ("base_commit", "source_refs", "expected"),
    [
        ("", ["event:blocked"], "base_commit is required"),
        ("abc123", ["git:abc123"], "full immutable Git commit"),
        ("a" * 40, ["event:blocked"], "must be bound by source_refs"),
    ],
)
def test_task_map_validation_rejects_unbound_successor_base(
    base_commit: str,
    source_refs: list[str],
    expected: str,
) -> None:
    task = {
        "task_id": "TASK-R2",
        "title": "continue a verified checkpoint",
        "owner_role": "dev",
        "allowed_paths": ["src/**"],
        "allowed_paths_reason": "bounded continuation scope",
        "verification": "git diff --check",
        "source_refs": source_refs,
        "payload": {"supersedes_task_ids": ["TASK-R1"]},
    }
    if base_commit:
        task["base_commit"] = base_commit

    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [task],
    })

    assert result.passed is False
    assert any(expected in error for error in result.errors)


def test_task_map_validation_accepts_source_bound_successor_base() -> None:
    base_commit = "a" * 40
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-R2",
            "title": "continue a verified checkpoint",
            "owner_role": "dev",
            "allowed_paths": ["src/**"],
            "allowed_paths_reason": "bounded continuation scope",
            "verification": "git diff --check",
            "base_commit": base_commit,
            "source_refs": ["event:blocked", f"git:{base_commit}"],
            "evidence_contract": {
                "supersedes_task_ids": ["TASK-R1"],
            },
        }],
    })

    assert result.passed is True, result.errors


def test_task_map_validation_rejects_noncanonical_continuation_base_alias() -> None:
    base_commit = "a" * 40
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-R1",
            "title": "retry from accepted checkpoint",
            "owner_role": "dev",
            "allowed_paths": ["src/**"],
            "allowed_paths_reason": "bounded retry scope",
            "verification": "git diff --check",
            "implementation_base_commit": base_commit,
            "source_refs": [f"git:{base_commit}"],
        }],
    })

    assert result.passed is False
    assert any(
        "implementation_base_commit is unsupported" in error
        for error in result.errors
    )


def test_task_map_validation_accepts_source_bound_same_id_retry_base() -> None:
    base_commit = "a" * 40
    result = validate_task_map_payload({
        "schema_version": "task-map.v1",
        "tasks": [{
            "task_id": "TASK-R1",
            "title": "retry from accepted checkpoint",
            "owner_role": "dev",
            "allowed_paths": ["src/**"],
            "allowed_paths_reason": "bounded retry scope",
            "verification": "git diff --check",
            "base_commit": base_commit,
            "source_refs": [f"git:{base_commit}"],
        }],
    })

    assert result.passed is True, result.errors
