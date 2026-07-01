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
