"""B11: zf issue ingest — 候选 → TaskContract 桥(doc 92 §4)。"""

from __future__ import annotations

from zf.cli.issue import _validate


def test_validate_fail_closed_on_missing_repro():
    fm = {
        "schema": "issue-candidate.v1",
        "bug_id": "B", "dedupe_key": "k", "title": "t",
        "allowed_paths": ["packages/x/**"],
    }
    errors = _validate(fm)
    assert any("repro_command" in e for e in errors)


def test_validate_fail_closed_on_missing_scope():
    fm = {
        "schema": "issue-candidate.v1",
        "bug_id": "B", "dedupe_key": "k", "title": "t",
        "repro_command": "pytest -q",
    }
    assert any("allowed_paths" in e for e in _validate(fm))


def test_validate_rejects_root_path_for_non_assembly():
    # R25 ISSUE-002 语义: 根级路径只许 assembly 类持有
    fm = {
        "schema": "issue-candidate.v1",
        "bug_id": "B", "dedupe_key": "k", "title": "t",
        "repro_command": "pytest -q",
        "allowed_paths": ["package.json"],
        "root_owner_class": "slice",
    }
    assert any("根级路径" in e for e in _validate(fm))
    fm["root_owner_class"] = "assembly"
    assert _validate(fm) == []


def test_validate_clean_candidate_passes():
    fm = {
        "schema": "issue-candidate.v1",
        "bug_id": "ZF-AR-BUG-X", "dedupe_key": "trace:stage", "title": "t",
        "repro_command": "pytest tests/x.py -q",
        "allowed_paths": ["packages/gw/**"],
        "root_owner_class": "slice",
    }
    assert _validate(fm) == []
