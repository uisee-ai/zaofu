"""批C(判据前置):C1 共享约定单源 / C2 验证层级校验。

回归钉:prd-goal e2e 的原始 task_map(textstat,含 `pip install -e`
切片验证)重放必须被拒——用真实事故做验收标准。
"""
from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.task_map import validate_task_map_payload

_ARCHIVE = Path("/home/user/workspace/avbs-refactor/e2e-archive-20260706-prd-goal/task_map.json")


def _task(task_id: str, paths: list[str], verification: list[str]) -> dict:
    return {
        "task_id": task_id, "title": task_id, "description": "d",
        "allowed_paths": paths, "allowed_paths_reason": "test",
        "verification": verification, "acceptance_criteria": ["ok"],
    }


# ---------- C2:验证层级 ----------

def test_c2_editable_install_in_slice_task_rejected() -> None:
    payload = {"tasks": [_task(
        "PKG-1", ["app/pyproject.toml"],
        ["cd app && python -m pip install -e ."],
    )]}
    result = validate_task_map_payload(payload)
    assert not result.passed
    assert any("system-level command" in e for e in result.errors)


def test_c2_pip_install_dot_rejected_and_plain_pytest_allowed() -> None:
    bad = validate_task_map_payload({"tasks": [_task(
        "PKG-1", ["app/pyproject.toml"], ["pip install ."],
    )]})
    assert any("system-level command" in e for e in bad.errors)
    ok = validate_task_map_payload({"tasks": [_task(
        "CORE-1", ["app/src/core.py", "app/tests/test_core.py"],
        ["pytest app/tests/test_core.py -q"],
    )]})
    assert not any("system-level command" in e for e in ok.errors)


def test_c2_regression_pin_original_textstat_task_map_rejected() -> None:
    # 归档 task_map 是 rescope 后终版;原始切片验证串取自当轮 rescope
    # 日志(reports/2026-07-06-prd-goal-mode-e2e.md finding-2 现场)。
    payload = {"tasks": [
        _task("TEXTSTAT-PACKAGING-001", ["app/pyproject.toml"],
              ["cd app && python -m pip install -e ."]),
        _task("TEXTSTAT-CLI-001",
              ["app/src/textstat/cli.py", "tests/test_cli.py"],
              ["cd app && python -m pip install -e . && textstat --help"]),
    ]}
    result = validate_task_map_payload(payload)
    assert not result.passed
    hits = [e for e in result.errors if "system-level command" in e]
    assert len(hits) >= 2  # 两个切片任务的 install 验证都被拒


# ---------- C1:共享约定单源 ----------

def test_c1_test_path_prefix_violation_rejected() -> None:
    payload = {
        "shared_conventions": {"test_path_prefix": "app/tests"},
        "tasks": [
            _task("CORE-1", ["app/src/core.py", "app/tests/test_core.py"],
                  ["pytest app/tests/test_core.py -q"]),
            _task("CLI-1", ["app/src/cli.py", "tests/test_cli.py"],  # 违约
                  ["pytest tests/test_cli.py -q"]),
        ],
    }
    result = validate_task_map_payload(payload)
    assert any("shared convention" in e and "CLI-1" in e for e in result.errors)


def test_c1_consistent_paths_pass_and_absent_conventions_noop() -> None:
    consistent = {
        "shared_conventions": {"test_path_prefix": "app/tests"},
        "tasks": [_task(
            "CORE-1", ["app/src/core.py", "app/tests/test_core.py"],
            ["pytest app/tests/test_core.py -q"],
        )],
    }
    assert not any(
        "shared convention" in e
        for e in validate_task_map_payload(consistent).errors
    )
    no_conventions = {"tasks": [_task(
        "CLI-1", ["app/src/cli.py", "tests/test_cli.py"],
        ["pytest tests/test_cli.py -q"],
    )]}
    assert not any(
        "shared convention" in e
        for e in validate_task_map_payload(no_conventions).errors
    )
