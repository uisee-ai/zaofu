"""P0-6(审计 D12):环境层 preflight 四探针 + environment 类事件。"""

from __future__ import annotations

from pathlib import Path

from zf.runtime.env_preflight import (
    check_browser_deps,
    check_hook_command,
    check_tmux,
    check_workdir_ownership,
    run_env_preflight,
)


def test_hook_command_healthy_for_real_zf() -> None:
    result = check_hook_command("/path/to/zaofu/.venv/bin/zf")
    assert result.ok, result.detail


def test_hook_command_detects_r20_shim_class(tmp_path: Path) -> None:
    # R20 事故类:命令存在但跑不动(指向无 zf 包的解释器)
    bad = tmp_path / "zf"
    bad.write_text("#!/bin/bash\nexec python3 -c 'import zf_nonexistent'\n")
    bad.chmod(0o755)
    result = check_hook_command(str(bad))
    assert result.ok is False and result.hard is True


def test_hook_command_missing_binary() -> None:
    result = check_hook_command("/nonexistent/zf-binary")
    assert result.ok is False and "不可执行" in result.detail


def test_tmux_probe() -> None:
    assert check_tmux().ok is True  # 本机有 tmux


def test_workdir_ownership_clean_and_missing(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    assert check_workdir_ownership(state_dir).ok is True  # 无 workdirs
    (state_dir / "workdirs" / "dev-1").mkdir(parents=True)
    (state_dir / "workdirs" / "dev-1" / "a.txt").write_text("x")
    assert check_workdir_ownership(state_dir).ok is True  # 全部本 uid


def test_browser_deps_only_when_declared(tmp_path: Path) -> None:
    assert check_browser_deps(tmp_path).ok is True  # 未声明 playwright


def test_run_env_preflight_shape(tmp_path: Path) -> None:
    checks = run_env_preflight(
        zf_cmd="/path/to/zaofu/.venv/bin/zf",
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
    )
    assert [c.name for c in checks] == [
        "hook_command", "tmux", "workdir_ownership", "browser_deps",
    ]
    assert all(c.ok for c in checks)
