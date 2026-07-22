"""Tests for provisioning gitignored runtime env into worktrees."""

from __future__ import annotations

from pathlib import Path

from zf.runtime.worktree_env import provision_worktree_env


def test_provisions_existing_path_as_symlink(tmp_path: Path):
    source = tmp_path / "main"
    (source / ".venv" / "bin").mkdir(parents=True)
    (source / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()

    provisioned = provision_worktree_env(worktree, source, [".venv", "node_modules"])

    assert provisioned == [".venv"]  # node_modules source missing -> skipped
    linked = worktree / ".venv"
    assert linked.is_symlink()
    assert (linked / "bin" / "python").exists()  # resolves through the symlink


def test_skips_missing_source_and_existing_dest(tmp_path: Path):
    source = tmp_path / "main"
    (source / ".venv").mkdir(parents=True)
    worktree = tmp_path / "wt"
    (worktree / ".venv").mkdir(parents=True)  # already present -> must not clobber

    provisioned = provision_worktree_env(worktree, source, [".venv", "missing"])

    assert provisioned == []
    assert not (worktree / ".venv").is_symlink()  # left the real dir alone


def test_idempotent(tmp_path: Path):
    source = tmp_path / "main"
    (source / "node_modules").mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    first = provision_worktree_env(worktree, source, ["node_modules"])
    second = provision_worktree_env(worktree, source, ["node_modules"])

    assert first == ["node_modules"]
    assert second == []  # already linked
    assert (worktree / "node_modules").is_symlink()


def test_bootstraps_uv_dev_env_before_symlink(tmp_path: Path):
    source = tmp_path / "main"
    source.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        assert Path(kwargs["cwd"]) == worktree
        (worktree / ".venv").mkdir()
        import subprocess

        return subprocess.CompletedProcess(argv, 0, "", "")

    provisioned = provision_worktree_env(
        worktree,
        source,
        [".venv"],
        bootstrap_uv_dev=True,
        runner=fake_run,
    )

    assert calls == [["uv", "sync", "--extra", "dev"]]
    assert provisioned == [".venv"]
    assert (worktree / ".venv").is_dir()
    assert not (worktree / ".venv").is_symlink()


# --- project.scripts.setup(项目自声明 worktree 就绪脚本)---


def test_run_project_setup_executes_and_marks(tmp_path: Path):
    from zf.runtime.worktree_env import SETUP_MARKER, run_project_setup

    worktree = tmp_path / "wt"
    worktree.mkdir()

    result = run_project_setup(worktree, "touch installed.flag")

    assert result.ran and result.ok and result.exit_code == 0
    assert (worktree / "installed.flag").exists()
    assert (worktree / SETUP_MARKER).exists()

    # 幂等:marker 匹配 → 不重跑
    (worktree / "installed.flag").unlink()
    again = run_project_setup(worktree, "touch installed.flag")
    assert not again.ran and again.ok
    assert not (worktree / "installed.flag").exists()


def test_run_project_setup_reruns_when_script_changes(tmp_path: Path):
    from zf.runtime.worktree_env import run_project_setup

    worktree = tmp_path / "wt"
    worktree.mkdir()
    run_project_setup(worktree, "touch a.flag")

    result = run_project_setup(worktree, "touch b.flag")

    assert result.ran and result.ok
    assert (worktree / "b.flag").exists()


def test_run_project_setup_reruns_when_dependency_manifest_changes(tmp_path: Path):
    from zf.runtime.worktree_env import run_project_setup

    worktree = tmp_path / "wt"
    worktree.mkdir()
    script = "touch installed.flag"
    run_project_setup(worktree, script)
    (worktree / "installed.flag").unlink()

    app = worktree / "app"
    app.mkdir()
    (app / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    rerun = run_project_setup(worktree, script)

    assert rerun.ran and rerun.ok
    assert (worktree / "installed.flag").exists()

    (worktree / "installed.flag").unlink()
    unchanged = run_project_setup(worktree, script)
    assert not unchanged.ran and unchanged.ok
    assert not (worktree / "installed.flag").exists()


def test_run_project_setup_supports_marker_outside_git_worktree(tmp_path: Path):
    from zf.runtime.worktree_env import SETUP_MARKER, run_project_setup

    worktree = tmp_path / "wt"
    marker_dir = tmp_path / "runtime-meta"
    worktree.mkdir()

    first = run_project_setup(
        worktree,
        "touch installed.flag",
        marker_dir=marker_dir,
    )
    (worktree / "installed.flag").unlink()
    second = run_project_setup(
        worktree,
        "touch installed.flag",
        marker_dir=marker_dir,
    )

    assert first.ran and first.ok
    assert not second.ran and second.ok
    assert (marker_dir / SETUP_MARKER).is_file()
    assert not (worktree / SETUP_MARKER).exists()


def test_run_project_setup_failure_is_surfaced_without_marker(tmp_path: Path):
    from zf.runtime.worktree_env import SETUP_MARKER, run_project_setup

    worktree = tmp_path / "wt"
    worktree.mkdir()

    result = run_project_setup(worktree, "echo boom >&2; exit 3")

    assert result.ran and not result.ok
    assert result.exit_code == 3
    assert "boom" in result.detail
    assert not (worktree / SETUP_MARKER).exists()  # 失败不得记成已就绪


def test_run_project_setup_no_declaration_is_noop(tmp_path: Path):
    from zf.runtime.worktree_env import run_project_setup

    worktree = tmp_path / "wt"
    worktree.mkdir()

    result = run_project_setup(worktree, "   ")

    assert not result.ran and result.ok
