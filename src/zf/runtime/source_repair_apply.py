"""Fail-closed application of a verified, single-commit harness repair."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Any


_HARD_DENY_NAMES = frozenset({
    ".env",
    "events.jsonl",
    "kanban.json",
    "session.yaml",
})


def apply_verified_checkpoint_repair(
    *,
    root: Path,
    worktree: Path,
    base_commit: str,
    repair_commit: str,
    checkpoint_id: str,
    safe_resume_action: str,
    validation_event_id: str,
    allow_paths: list[str],
    deny_paths: list[str],
) -> dict[str, Any]:
    """Cherry-pick one verified repair when every immutable precondition holds."""

    root = Path(root).resolve()
    worktree = Path(worktree).resolve()
    required = {
        "base_commit": str(base_commit or "").strip(),
        "repair_commit": str(repair_commit or "").strip(),
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "safe_resume_action": str(safe_resume_action or "").strip(),
        "validation_event_id": str(validation_event_id or "").strip(),
    }
    missing = sorted(key for key, value in required.items() if not value)
    if missing:
        return _blocked("missing_required_identity", missing=missing)
    if not _is_git_root(root) or not _is_git_root(worktree):
        return _blocked("repair_repository_missing")

    root_status = _git(root, "status", "--porcelain", "--untracked-files=all")
    if not root_status["ok"]:
        return _blocked("source_status_failed", detail=root_status["stderr"])
    if root_status["stdout"].strip():
        return _blocked("source_checkout_dirty")
    repair_status = _git(worktree, "status", "--porcelain", "--untracked-files=all")
    if not repair_status["ok"]:
        return _blocked("repair_status_failed", detail=repair_status["stderr"])
    if repair_status["stdout"].strip():
        return _blocked("repair_worktree_dirty")

    root_head = _git_text(root, "rev-parse", "HEAD")
    repair_head = _git_text(worktree, "rev-parse", "HEAD")
    if root_head != required["base_commit"]:
        return _blocked(
            "source_head_drift",
            expected=required["base_commit"],
            actual=root_head,
        )
    if repair_head != required["repair_commit"]:
        return _blocked(
            "repair_head_drift",
            expected=required["repair_commit"],
            actual=repair_head,
        )
    ancestor = _git(
        worktree,
        "merge-base",
        "--is-ancestor",
        required["base_commit"],
        required["repair_commit"],
    )
    if not ancestor["ok"]:
        return _blocked("repair_base_not_ancestor")
    try:
        commit_count = int(
            _git_text(
                worktree,
                "rev-list",
                "--count",
                f"{required['base_commit']}..{required['repair_commit']}",
            )
            or "0"
        )
    except ValueError:
        commit_count = 0
    if commit_count != 1:
        return _blocked("repair_commit_count_invalid", commit_count=commit_count)

    changed_files = _git_lines(
        worktree,
        "diff",
        "--name-only",
        f"{required['base_commit']}..{required['repair_commit']}",
    )
    if not changed_files:
        return _blocked("repair_diff_empty")
    denied = [
        path for path in changed_files
        if _hard_denied(path) or _matches_any(path, deny_paths)
    ]
    if denied:
        return _blocked("repair_path_denied", paths=denied)
    outside = [path for path in changed_files if not _matches_any(path, allow_paths)]
    if outside:
        return _blocked("repair_path_outside_allowlist", paths=outside)

    applied = _git(root, "cherry-pick", required["repair_commit"])
    if not applied["ok"]:
        _git(root, "cherry-pick", "--abort")
        return _blocked("repair_cherry_pick_failed", detail=applied["stderr"])
    applied_commit = _git_text(root, "rev-parse", "HEAD")
    from zf.runtime.load_receipt import source_tree_sha256

    return {
        "ok": True,
        "status": "applied",
        "schema_version": "source-repair.apply-receipt.v1",
        "base_commit": required["base_commit"],
        "repair_commit": required["repair_commit"],
        "applied_commit": applied_commit,
        "source_root": str(root),
        "source_tree_sha256": source_tree_sha256(root),
        "checkpoint_id": required["checkpoint_id"],
        "safe_resume_action": required["safe_resume_action"],
        "validation_event_id": required["validation_event_id"],
        "changed_files": changed_files,
    }


def _blocked(reason: str, **detail: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "blocked",
        "schema_version": "source-repair.apply-receipt.v1",
        "reason": reason,
        **detail,
    }


def _hard_denied(path: str) -> bool:
    parts = Path(path).parts
    return any(part in _HARD_DENY_NAMES for part in parts)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, str(pattern).strip())
        for pattern in patterns
        if str(pattern).strip()
    )


def _is_git_root(path: Path) -> bool:
    top = _git_text(path, "rev-parse", "--show-toplevel") if path.is_dir() else ""
    return bool(top) and Path(top).resolve() == path.resolve()


def _git_text(cwd: Path, *args: str) -> str:
    result = _git(cwd, *args)
    return result["stdout"].strip() if result["ok"] else ""


def _git_lines(cwd: Path, *args: str) -> list[str]:
    return [line.strip() for line in _git_text(cwd, *args).splitlines() if line.strip()]


def _git(cwd: Path, *args: str) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


__all__ = ["apply_verified_checkpoint_repair"]
