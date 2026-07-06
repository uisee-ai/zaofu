from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.artifact_matrix_gate import evaluate_artifact_matrix_gate


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_artifact_matrix_gate_passes_generic_config(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text("ok\n", encoding="utf-8")
    _write_json(
        tmp_path / "matrix.json",
        {"rows": [
            {
                "id": "CAP-1",
                "priority": "P0",
                "status": "done",
                "source_refs": ["src/app.ts"],
                "evidence_refs": ["report.md"],
            },
            {"id": "CAP-2", "priority": "P1", "status": "planned"},
        ]},
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "required_artifacts": ["report.md"],
        "matrix_paths": ["matrix.json"],
        "blocking_priority": "P0",
        "allowed_statuses": ["done"],
        "required_row_fields": ["id", "source_refs", "evidence_refs"],
    })

    assert result.passed is True
    assert result.blocking_rows == 1
    assert result.checked_rows == 2


def test_artifact_matrix_gate_fails_missing_artifact_and_bad_blocking_row(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "matrix.json",
        {"rows": [
            {
                "id": "CAP-1",
                "priority": "P0",
                "status": "partial",
                "source_refs": [],
            },
        ]},
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "required_artifacts": ["missing.md"],
        "matrix_paths": ["matrix.json"],
        "blocking_priority": "P0",
        "allowed_statuses": ["done"],
        "required_row_fields": ["source_refs", "evidence_refs"],
    })

    codes = {finding.code for finding in result.findings}
    assert result.passed is False
    assert "required_artifact_missing" in codes
    assert "matrix_row_status_not_allowed" in codes
    assert "matrix_row_required_field_missing" in codes


def test_artifact_matrix_gate_requires_one_field_from_group_for_blocking_row(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "matrix.json",
        {"rows": [
            {
                "id": "CAP-1",
                "priority": "P0",
                "status": "passed",
                "source_refs": ["src/app.ts"],
                "evidence_refs": ["reports/app.md"],
                "verification": ["pnpm test"],
            },
            {
                "id": "CAP-2",
                "priority": "P0",
                "status": "passed",
                "source_refs": ["src/worker.ts"],
                "evidence_refs": ["reports/worker.md"],
            },
        ]},
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "matrix_paths": ["matrix.json"],
        "blocking_priority": "P0",
        "allowed_statuses": ["passed"],
        "required_row_fields": ["source_refs", "evidence_refs"],
        "required_row_field_groups": [
            ["verification", "verify_commands", "verification_commands"],
        ],
    })

    assert result.passed is False
    assert any(
        finding.code == "matrix_row_required_field_group_missing"
        and finding.row_id == "CAP-2"
        for finding in result.findings
    )


def test_artifact_matrix_gate_loads_config_ref_and_blocks_forbidden_text(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "gate.json",
        {
            "matrix_paths": ["matrix.json"],
            "blocking_priority": "P0",
            "allowed_statuses": ["done"],
            "forbidden_text": [
                {"path": "src/web.ts", "contains": "TODO placeholder"}
            ],
        },
    )
    _write_json(
        tmp_path / "matrix.json",
        [{"id": "CAP-1", "priority": "P0", "status": "done"}],
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "web.ts").write_text("TODO placeholder\n", encoding="utf-8")

    result = evaluate_artifact_matrix_gate(tmp_path, {"config_ref": "gate.json"})

    assert result.passed is False
    assert [finding.code for finding in result.findings] == ["forbidden_text_present"]


def test_artifact_matrix_gate_fails_closed_when_config_ref_is_missing(
    tmp_path: Path,
) -> None:
    result = evaluate_artifact_matrix_gate(
        tmp_path,
        {"config_ref": "docs/plans/missing-gate.json"},
    )

    assert result.passed is False
    assert result.findings[0].code == "gate_config_missing"


def test_artifact_matrix_gate_blocks_open_module_parity_gaps(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "docs/validation/module-parity/web-dashboard.json",
        {
            "schema_version": "module-parity-report.v1",
            "module_id": "web-dashboard",
            "parent_task_id": "CANGJIE-WEB-001",
            "affinity_tag": "web-tui",
            "lane_id": "lane-4",
            "source_paths": ["source/web"],
            "target_paths": ["web/src"],
            "capability_rows": [{"id": "WEBCHAT", "priority": "P0", "status": "partial"}],
            "test_rows": [{"id": "WEBCHAT-E2E", "priority": "P0", "status": "missing"}],
            "runtime_evidence_refs": ["reports/webchat.md"],
            "gap_tasks": [{"task_id": "CANGJIE-WEB-GAP-001"}],
            "open_p0_p1_gap_count": 1,
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "module_parity_report_paths": [
            "docs/validation/module-parity/web-dashboard.json",
        ],
        "runtime_path_evidence_modules": ["web-dashboard"],
    })

    codes = {finding.code for finding in result.findings}
    assert result.passed is False
    assert "module_parity_open_gaps" in codes


def test_artifact_matrix_gate_rejects_project_path_aliases_by_default(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "docs/validation/module-parity/web-dashboard.json",
        {
            "schema_version": "module-parity-report.v1",
            "module_id": "web-dashboard",
            "parent_task_id": "WEB-001",
            "affinity_tag": "web-tui",
            "lane_id": "lane-4",
            "hermes_original_paths": ["legacy-source/web"],
            "cangjie_target_paths": ["web/src"],
            "capability_rows": [{"id": "WEBCHAT", "priority": "P0", "status": "done"}],
            "test_rows": [{"id": "WEBCHAT-E2E", "priority": "P0", "status": "done"}],
            "runtime_evidence_refs": ["reports/webchat.md"],
            "gap_tasks": [{"task_id": "WEB-GAP-CLOSED", "status": "closed"}],
            "open_p0_p1_gap_count": 0,
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "module_parity_report_paths": [
            "docs/validation/module-parity/web-dashboard.json",
        ],
    })

    assert result.passed is False
    missing = {
        finding.message
        for finding in result.findings
        if finding.code == "module_parity_required_field_missing"
    }
    assert any("source_paths" in item for item in missing)
    assert any("target_paths" in item for item in missing)


def test_artifact_matrix_gate_accepts_legacy_module_parity_path_aliases_when_enabled(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "docs/validation/module-parity/web-dashboard.json",
        {
            "schema_version": "module-parity-report.v1",
            "module_id": "web-dashboard",
            "parent_task_id": "WEB-001",
            "affinity_tag": "web-tui",
            "lane_id": "lane-4",
            "hermes_original_paths": ["legacy-source/web"],
            "cangjie_target_paths": ["web/src"],
            "capability_rows": [{"id": "WEBCHAT", "priority": "P0", "status": "done"}],
            "test_rows": [{"id": "WEBCHAT-E2E", "priority": "P0", "status": "done"}],
            "runtime_evidence_refs": ["reports/webchat.md"],
            "gap_tasks": [{"task_id": "WEB-GAP-CLOSED", "status": "closed"}],
            "open_p0_p1_gap_count": 0,
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "module_parity_report_paths": [
            "docs/validation/module-parity/web-dashboard.json",
        ],
        "legacy_module_parity_field_aliases": True,
    })

    assert result.passed is True


def test_artifact_matrix_gate_accepts_configured_module_parity_aliases(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "docs/validation/module-parity/web-dashboard.json",
        {
            "schema_version": "module-parity-report.v1",
            "module_id": "web-dashboard",
            "parent_task_id": "WEB-001",
            "affinity_tag": "web-tui",
            "lane_id": "lane-4",
            "project_source_refs": ["source/web"],
            "project_target_refs": ["web/src"],
            "capability_rows": [{"id": "WEBCHAT", "priority": "P0", "status": "done"}],
            "test_rows": [{"id": "WEBCHAT-E2E", "priority": "P0", "status": "done"}],
            "runtime_evidence_refs": ["reports/webchat.md"],
            "gap_tasks": [{"task_id": "WEB-GAP-CLOSED", "status": "closed"}],
            "open_p0_p1_gap_count": 0,
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "module_parity_report_paths": [
            "docs/validation/module-parity/web-dashboard.json",
        ],
        "module_parity_field_aliases": {
            "source_paths": ["project_source_refs"],
            "target_paths": ["project_target_refs"],
        },
    })

    assert result.passed is True


def test_artifact_matrix_gate_validates_gap_task_map_schema(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "docs/validation/cangjie-gap-task-map.json",
        {
            "schema_version": "module-gap-plan.v1",
            "module_id": "web-dashboard",
            "gap_tasks": [{
                "task_id": "CANGJIE-WEB-GAP-001",
                "source_refs": ["hermes-agent/web"],
            }],
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "gap_task_map_paths": ["docs/validation/cangjie-gap-task-map.json"],
    })

    assert result.passed is False
    assert result.findings[0].code == "gap_task_map_invalid"
    assert "claim_paths is required" in result.findings[0].message


def test_artifact_matrix_gate_validates_goal_gap_task_map_schema(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "docs/validation/goal-gap-plan.json",
        {
            "schema_version": "goal-gap-plan.v1",
            "goal_kind": "issue",
            "gap_category": "issue_gap",
            "gap_tasks": [{
                "task_id": "ISSUE-123-GAP-001",
                "source_refs": ["issues/123.md"],
            }],
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "goal_gap_task_map_paths": ["docs/validation/goal-gap-plan.json"],
    })

    assert result.passed is False
    assert result.findings[0].code == "goal_gap_task_map_invalid"
    assert "claim_paths is required" in result.findings[0].message


def test_artifact_matrix_gate_blocks_open_goal_gaps(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "reports/issue-123/goal-gap-report.json",
        {
            "schema_version": "goal-gap-report.v1",
            "goal_id": "ISSUE-123",
            "goal_kind": "issue",
            "gap_category": "issue_gap",
            "open_p0_p1_gap_count": 1,
            "evidence_refs": ["reports/issue-123/repro.md"],
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "goal_gap_report_paths": ["reports/issue-123/goal-gap-report.json"],
        "goal_gap_required_fields": ["goal_id", "goal_kind", "gap_category", "evidence_refs"],
    })

    codes = {finding.code for finding in result.findings}
    assert result.passed is False
    assert "goal_gap_open_gaps" in codes


def test_artifact_matrix_gate_passes_closed_goal_gap_report(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "reports/prd-1/goal-gap-report.json",
        {
            "schema_version": "goal-gap-report.v1",
            "goal_id": "PRD-1",
            "goal_kind": "prd",
            "gap_category": "acceptance_gap",
            "open_p0_p1_gap_count": 0,
            "evidence_refs": ["reports/prd-1/e2e.md"],
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "goal_gap_report_paths": ["reports/prd-1/goal-gap-report.json"],
        "goal_gap_required_fields": ["goal_id", "goal_kind", "gap_category", "evidence_refs"],
    })

    assert result.passed is True


def test_artifact_matrix_gate_blocks_unmapped_inventory_items(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "docs/plans/hermes-tool-inventory.json",
        {
            "items": [
                {
                    "id": "TOOL-SEARCH",
                    "priority": "P0",
                    "source_refs": ["hermes/tools/search.py"],
                },
                {
                    "id": "TOOL-MCP",
                    "priority": "P0",
                    "source_refs": ["hermes/tools/mcp_tool.py"],
                },
            ]
        },
    )
    _write_json(
        tmp_path / "docs/plans/cangjie-acceptance-matrix.json",
        {
            "rows": [
                {
                    "id": "ACC-TOOL-SEARCH",
                    "inventory_id": "TOOL-SEARCH",
                    "priority": "P0",
                    "status": "passed",
                    "source_refs": ["hermes/tools/search.py"],
                    "evidence_refs": ["packages/tools/src/search.ts"],
                }
            ]
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "matrix_paths": ["docs/plans/cangjie-acceptance-matrix.json"],
        "blocking_priorities": ["P0", "P1"],
        "allowed_statuses": ["passed"],
        "required_row_fields": ["source_refs", "evidence_refs"],
        "inventory_coverage": {
            "inventory_refs": ["docs/plans/hermes-tool-inventory.json"],
            "matrix_paths": ["docs/plans/cangjie-acceptance-matrix.json"],
            "required_priorities": ["P0", "P1"],
            "require_all_inventory_items_mapped": True,
        },
    })

    assert result.passed is False
    assert any(
        finding.code == "inventory_item_unmapped"
        and finding.row_id == "TOOL-MCP"
        for finding in result.findings
    )


def test_artifact_matrix_gate_blocks_inventory_downgraded_to_nonblocking_row(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "docs/plans/hermes-provider-profile-inventory.json",
        {
            "items": [{
                "id": "PROVIDER-DOUBAO",
                "priority": "P0",
                "source_refs": ["hermes/providers/doubao.py"],
            }]
        },
    )
    _write_json(
        tmp_path / "docs/plans/cangjie-acceptance-matrix.json",
        {
            "rows": [{
                "id": "ACC-PROVIDER-DOUBAO",
                "inventory_id": "PROVIDER-DOUBAO",
                "priority": "P2",
                "status": "deferred",
                "source_refs": ["hermes/providers/doubao.py"],
                "evidence_refs": ["docs/validation/deferred.md"],
            }]
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "matrix_paths": ["docs/plans/cangjie-acceptance-matrix.json"],
        "blocking_priorities": ["P0", "P1"],
        "allowed_statuses": ["passed"],
        "required_row_fields": ["source_refs", "evidence_refs"],
        "inventory_coverage": {
            "inventory_refs": ["docs/plans/hermes-provider-profile-inventory.json"],
            "matrix_paths": ["docs/plans/cangjie-acceptance-matrix.json"],
            "required_priorities": ["P0", "P1"],
            "require_blocking_matrix_for_blocking_inventory": True,
        },
    })

    codes = {finding.code for finding in result.findings}
    assert result.passed is False
    assert "matrix_no_blocking_rows" in codes
    assert any(
        finding.code == "inventory_item_not_blocking"
        and finding.row_id == "PROVIDER-DOUBAO"
        for finding in result.findings
    )


def test_artifact_matrix_gate_allows_non_required_inventory_priority(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "docs/plans/hermes-tool-inventory.json",
        {
            "items": [
                {
                    "id": "TOOL-SEARCH",
                    "priority": "P0",
                    "source_refs": ["hermes/tools/search.py"],
                },
                {
                    "id": "TOOL-EXPERIMENTAL",
                    "priority": "P2",
                    "source_refs": ["hermes/tools/experimental.py"],
                },
            ]
        },
    )
    _write_json(
        tmp_path / "docs/plans/cangjie-acceptance-matrix.json",
        {
            "rows": [{
                "id": "ACC-TOOL-SEARCH",
                "inventory_id": "TOOL-SEARCH",
                "priority": "P0",
                "status": "passed",
                "source_refs": ["hermes/tools/search.py"],
                "evidence_refs": ["packages/tools/src/search.ts"],
            }]
        },
    )

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "matrix_paths": ["docs/plans/cangjie-acceptance-matrix.json"],
        "blocking_priorities": ["P0", "P1"],
        "allowed_statuses": ["passed"],
        "required_row_fields": ["source_refs", "evidence_refs"],
        "inventory_coverage": {
            "inventory_refs": ["docs/plans/hermes-tool-inventory.json"],
            "matrix_paths": ["docs/plans/cangjie-acceptance-matrix.json"],
            "required_priorities": ["P0", "P1"],
            "require_all_inventory_items_mapped": True,
        },
    })

    assert result.passed is True
