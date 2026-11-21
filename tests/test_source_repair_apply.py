from __future__ import annotations

import subprocess
from pathlib import Path

from zf.runtime.source_repair_apply import apply_verified_checkpoint_repair


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repair_repositories(tmp_path: Path, *, changed_path: str = "src/zf/fix.py"):
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    base_commit = _git(root, "rev-parse", "HEAD")
    worktree = tmp_path / "repair"
    _git(root, "worktree", "add", "-q", "-b", "repair", str(worktree), "HEAD")
    target = worktree / changed_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("fixed = True\n", encoding="utf-8")
    _git(worktree, "add", changed_path)
    _git(worktree, "commit", "-q", "-m", "fix: repair harness")
    repair_commit = _git(worktree, "rev-parse", "HEAD")
    return root, worktree, base_commit, repair_commit


def _apply(root: Path, worktree: Path, base_commit: str, repair_commit: str):
    return apply_verified_checkpoint_repair(
        root=root,
        worktree=worktree,
        base_commit=base_commit,
        repair_commit=repair_commit,
        checkpoint_id="checkpoint-1",
        safe_resume_action="needs_stage_dispatch",
        validation_event_id="evt-validation-1",
        allow_paths=["src/zf/**", "tests/**"],
        deny_paths=[".env", "**/events.jsonl", "**/session.yaml"],
    )


def test_verified_checkpoint_repair_applies_one_clean_allowed_commit(tmp_path: Path):
    root, worktree, base_commit, repair_commit = _repair_repositories(tmp_path)

    result = _apply(root, worktree, base_commit, repair_commit)

    assert result["ok"] is True
    assert result["base_commit"] == base_commit
    assert result["repair_commit"] == repair_commit
    assert result["applied_commit"] == _git(root, "rev-parse", "HEAD")
    assert result["changed_files"] == ["src/zf/fix.py"]
    assert (root / "src/zf/fix.py").read_text(encoding="utf-8") == "fixed = True\n"


def test_verified_checkpoint_repair_rejects_hard_denied_path(tmp_path: Path):
    root, worktree, base_commit, repair_commit = _repair_repositories(
        tmp_path,
        changed_path="src/zf/events.jsonl",
    )

    result = _apply(root, worktree, base_commit, repair_commit)

    assert result["ok"] is False
    assert result["reason"] == "repair_path_denied"
    assert _git(root, "rev-parse", "HEAD") == base_commit


def test_verified_checkpoint_repair_rejects_dirty_or_drifted_source(tmp_path: Path):
    root, worktree, base_commit, repair_commit = _repair_repositories(tmp_path)
    (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    dirty = _apply(root, worktree, base_commit, repair_commit)

    assert dirty["reason"] == "source_checkout_dirty"
    (root / "dirty.txt").unlink()
    (root / "other.txt").write_text("other\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-q", "-m", "chore: move source head")

    drifted = _apply(root, worktree, base_commit, repair_commit)

    assert drifted["reason"] == "source_head_drift"


def test_verified_checkpoint_repair_rejects_dirty_repair_worktree(tmp_path: Path):
    root, worktree, base_commit, repair_commit = _repair_repositories(tmp_path)
    (worktree / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    result = _apply(root, worktree, base_commit, repair_commit)

    assert result["reason"] == "repair_worktree_dirty"
    assert _git(root, "rev-parse", "HEAD") == base_commit


def test_verified_checkpoint_repair_rejects_path_outside_allowlist(tmp_path: Path):
    root, worktree, base_commit, repair_commit = _repair_repositories(
        tmp_path,
        changed_path="README.md",
    )

    result = _apply(root, worktree, base_commit, repair_commit)

    assert result["reason"] == "repair_path_outside_allowlist"
    assert result["paths"] == ["README.md"]
    assert _git(root, "rev-parse", "HEAD") == base_commit


def test_verified_checkpoint_repair_requires_exactly_one_commit(tmp_path: Path):
    root, worktree, base_commit, _repair_commit = _repair_repositories(tmp_path)
    (worktree / "tests").mkdir()
    (worktree / "tests/test_fix.py").write_text("def test_fix(): pass\n", encoding="utf-8")
    _git(worktree, "add", "tests/test_fix.py")
    _git(worktree, "commit", "-q", "-m", "test: cover repair")
    repair_commit = _git(worktree, "rev-parse", "HEAD")

    result = _apply(root, worktree, base_commit, repair_commit)

    assert result["reason"] == "repair_commit_count_invalid"
    assert result["commit_count"] == 2


def test_repair_apply_rejects_repository_subdirectory(tmp_path: Path):
    root, worktree, base, commit = _repair_repositories(tmp_path)
    result = _apply(root, worktree / "src", base, commit)
    assert result["reason"] == "repair_repository_missing"
    assert _git(root, "rev-parse", "HEAD") == base
