"""zf stop --clean-workdirs — state-dir-scoped worktree/branch cleanup.

2026-07-10 E2E: zf stop left worker worktrees/branches behind, so the next
flow sharing the product repo died on "worker/dev-lane-0 is already used by
worktree ...". Cleanup ownership must be precise: only worktrees under the
stopping flow's state dir (and the branches they held) are removed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from zf.cli.stop import clean_state_dir_workdirs


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
    )


def _init_product_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")


def _add_worker_worktree(root: Path, state_dir: str, lane: str) -> Path:
    path = root / state_dir / "workdirs" / lane / "project"
    path.parent.mkdir(parents=True, exist_ok=True)
    result = _git(root, "worktree", "add", str(path), "-b", f"worker/{lane}", "main")
    assert result.returncode == 0, result.stderr
    return path


def test_clean_removes_only_own_state_dir_worktrees_and_branches(tmp_path: Path):
    _init_product_repo(tmp_path)
    mine = _add_worker_worktree(tmp_path, ".zf-a", "dev-lane-0")
    other = _add_worker_worktree(tmp_path, ".zf-b", "dev-lane-1")

    removed, deleted = clean_state_dir_workdirs(tmp_path, tmp_path / ".zf-a")

    assert removed == 1
    assert deleted == ["worker/dev-lane-0"]
    assert not mine.exists()
    assert other.exists()
    branches = _git(tmp_path, "branch", "--list", "worker/*").stdout
    assert "worker/dev-lane-0" not in branches
    assert "worker/dev-lane-1" in branches


def test_clean_unblocks_next_flow_reusing_the_branch_name(tmp_path: Path):
    """The exact E2E collision: after flow A stops, flow B must be able to
    check out worker/dev-lane-0 again."""
    _init_product_repo(tmp_path)
    _add_worker_worktree(tmp_path, ".zf-a", "dev-lane-0")

    clean_state_dir_workdirs(tmp_path, tmp_path / ".zf-a")

    reuse = tmp_path / ".zf-b" / "workdirs" / "dev-lane-0" / "project"
    reuse.parent.mkdir(parents=True, exist_ok=True)
    result = _git(
        tmp_path, "worktree", "add", str(reuse), "-b", "worker/dev-lane-0", "main",
    )
    assert result.returncode == 0, result.stderr


def test_clean_is_noop_without_matching_worktrees(tmp_path: Path):
    _init_product_repo(tmp_path)
    _add_worker_worktree(tmp_path, ".zf-b", "dev-lane-1")

    removed, deleted = clean_state_dir_workdirs(tmp_path, tmp_path / ".zf-a")

    assert removed == 0
    assert deleted == []
