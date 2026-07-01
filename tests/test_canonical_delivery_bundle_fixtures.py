from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.task_map import (
    load_source_index,
    load_task_map,
    validate_coverage_report_payload,
    validate_source_index_payload,
    validate_task_map_payload,
)


REPO_ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "canonical_delivery_bundle"


def _bundle_dirs() -> list[Path]:
    return sorted(path.parent for path in FIXTURE_ROOT.rglob("task_map.json"))


def test_canonical_delivery_bundle_fixtures_validate() -> None:
    bundle_dirs = _bundle_dirs()
    assert bundle_dirs

    for bundle_dir in bundle_dirs:
        task_map = load_task_map(bundle_dir / "task_map.json")
        source_index = load_source_index(bundle_dir / "source_index.json")
        coverage_report = json.loads(
            (bundle_dir / "coverage_report.json").read_text(encoding="utf-8")
        )

        task_map_result = validate_task_map_payload(
            task_map,
            require_task_verification=True,
        )
        assert task_map_result.passed, (bundle_dir, task_map_result.errors)

        source_result = validate_source_index_payload(
            source_index,
            task_map=task_map,
            require_canonical=True,
        )
        assert source_result.passed, (bundle_dir, source_result.errors)

        coverage_result = validate_coverage_report_payload(
            coverage_report,
            task_map=task_map,
        )
        assert coverage_result.passed, (bundle_dir, coverage_result.errors)
        assert not coverage_report.get("unresolved_unknowns")


def test_coverage_report_rejects_noncanonical_empty_task_coverage() -> None:
    task_map = {
        "schema_version": "task-map.v1",
        "tasks": [{"task_id": "TASK-A"}],
    }
    coverage_report = {
        "schema_version": "coverage-report.v1",
        "coverage": "complete",
        "requirements_covered": ["TASK-A"],
        "unresolved_unknowns": [],
    }

    result = validate_coverage_report_payload(
        coverage_report,
        task_map=task_map,
    )

    assert result.passed is False
    assert "tasks must be a non-empty list or object" in result.errors
    assert result.summary["coverage_task_count"] == 0


def test_coverage_report_must_cover_task_map_task_ids() -> None:
    task_map = {
        "schema_version": "task-map.v1",
        "tasks": [{"task_id": "TASK-A"}, {"task_id": "TASK-B"}],
    }
    coverage_report = {
        "schema_version": "coverage-report.v1",
        "tasks": [{"task_id": "TASK-A", "source_status": "covered"}],
        "unresolved_unknowns": [],
    }

    result = validate_coverage_report_payload(
        coverage_report,
        task_map=task_map,
    )

    assert result.passed is False
    assert "coverage_report missing task_id 'TASK-B'" in result.errors
    assert result.summary["missing_task_ids"] == ["TASK-B"]


def test_canonical_delivery_bundle_source_refs_point_to_fixture_files() -> None:
    for bundle_dir in _bundle_dirs():
        source_index = load_source_index(bundle_dir / "source_index.json")
        entries = source_index["tasks"].values() if isinstance(source_index["tasks"], dict) else source_index["tasks"]
        for entry in entries:
            source_ref = str(entry["source_ref"])
            source_path = source_ref.split("#", 1)[0]
            assert (REPO_ROOT / source_path).exists(), source_ref
