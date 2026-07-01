"""Tests for git state capture."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.core.state.git_state import GitState
from zf.runtime.git_capture import capture_files_touched_since, capture_git_state


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin",
        },
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def test_capture_in_clean_repo_returns_branch_and_head(git_repo: Path):
    state = capture_git_state(git_repo)
    assert state.branch == "main"
    assert state.head and len(state.head) == 40
    assert state.dirty_files == []
    assert "initial" in state.last_commit_msg
    assert state.ts


def test_capture_detects_dirty_files(git_repo: Path):
    (git_repo / "new.txt").write_text("x")
    (git_repo / "README.md").write_text("modified\n")
    state = capture_git_state(git_repo)
    paths = set(state.dirty_files)
    assert "new.txt" in paths
    assert "README.md" in paths


def test_capture_expands_untracked_directories_to_files(git_repo: Path):
    nested = git_repo / "docs" / "plans"
    nested.mkdir(parents=True)
    (nested / "plan.md").write_text("draft\n")

    state = capture_git_state(git_repo)

    assert "docs/plans/plan.md" in state.dirty_files
    assert "docs/" not in state.dirty_files


def test_capture_filters_harness_runtime_state_and_launch_logs(git_repo: Path):
    runtime_dir = git_repo / ".zf-cj-min-lane-pipeline-r36-20260621"
    runtime_dir.mkdir()
    (runtime_dir / "events.jsonl").write_text("{}\n")
    (git_repo / "start-r36-CJMIN.log").write_text("boot\n")
    (git_repo / "webkanban-8001-r36.log").write_text("web\n")
    (git_repo / "autoresearch-resident-r36.log").write_text("loop\n")
    (git_repo / "src").mkdir()
    (git_repo / "src" / "app.ts").write_text("export {}\n")

    state = capture_git_state(git_repo)
    touched = capture_files_touched_since(git_repo)

    assert state.dirty_files == ["src/app.ts"]
    assert touched == ["src/app.ts"]


def test_capture_outside_git_repo_returns_empty_state(tmp_path: Path):
    # tmp_path is NOT a git repo
    state = capture_git_state(tmp_path)
    assert state.branch is None
    assert state.head is None
    assert state.dirty_files == []
    assert state.last_commit_msg == ""
    assert state.ts  # always populated


def test_state_is_serializable_to_dict(git_repo: Path):
    from dataclasses import asdict
    state = capture_git_state(git_repo)
    d = asdict(state)
    assert "branch" in d
    assert "head" in d
    assert "dirty_files" in d
    assert "last_commit_msg" in d
    assert "ts" in d
