"""Stop an experiment's runtime independently of candidate/archive retention."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import isolated_project_subprocess_env
from zf.core.state.session import SessionStore, ZfNotInitialized


def stop_experiment_runtime(
    worktree: Path, *, runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if worktree.is_symlink():
        return {"status": "retained", "reason": "worktree_symlink"}
    root = worktree.resolve()
    config_path = root / "zf.yaml"
    if not config_path.is_file():
        return {"status": "not_started"}
    try:
        from zf.autoresearch.owned_runtime_state import owned_runtime_state
        state = owned_runtime_state(root)
        if not (state / "session.yaml").is_file():
            return {"status": "not_started"}
        session = SessionStore(state / "session.yaml").load()
        if session is None or Path(session.project_root).resolve() != root:
            return {"status": "retained", "reason": "runtime_owner_mismatch"}
        outcome = runner(
            [sys.executable, "-m", "zf.cli.main", "stop", "--fast", "--include-run-manager"],
            cwd=root, env=isolated_project_subprocess_env(), capture_output=True,
            text=True, check=False, timeout=90,
        )
        return {"status": "stopped" if outcome.returncode == 0 else "retained",
                "returncode": outcome.returncode,
                "reason": "" if outcome.returncode == 0 else "runtime_stop_failed"}
    except (OSError, ValueError, TypeError, ConfigError, ZfNotInitialized, yaml.YAMLError, subprocess.TimeoutExpired) as exc:
        return {"status": "retained", "reason": type(exc).__name__,
                "phase": "runtime_cleanup", "next_owner": "run_manager"}


def finalize_resident_worktree(
    *,
    worktree: Path,
    loop_request_id: str,
    archive_required: bool = False,
    archive_manifest: Path | None = None,
) -> dict[str, Any]:
    """Remove only clean terminal worktrees; retain every candidate diff."""

    path = Path(worktree).resolve()
    base = {
        "loop_request_id": loop_request_id,
        "worktree": str(path),
    }
    if not path.exists():
        return {**base, "status": "missing"}
    if archive_required and (
        archive_manifest is None or not Path(archive_manifest).is_file()
    ):
        return {
            **base,
            "status": "retained",
            "reason": "archive_missing",
            "event_type": "autoresearch.worktree.retained",
        }
    from zf.autoresearch.worktree_preparation import (
        cleanup_interrupted_prepared_worktree,
    )

    preparation_cleanup = cleanup_interrupted_prepared_worktree(worktree=path)
    if preparation_cleanup.get("status") not in {"not_prepared", "not_needed"}:
        base["preparation_cleanup"] = preparation_cleanup

    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        return {
            **base,
            "status": "retained",
            "reason": "git_status_failed",
            "stderr_tail": (status.stderr or "")[-1000:],
            "event_type": "autoresearch.worktree.retained",
        }
    branch_proc = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=False,
    )
    branch = (branch_proc.stdout or "").strip()
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        return {
            **base,
            "status": "retained",
            "reason": "candidate_diff_present",
            "branch": branch,
            "dirty_paths": dirty[:100],
            "event_type": "autoresearch.worktree.retained",
        }

    common_proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    common_dir = (common_proc.stdout or "").strip()
    if common_proc.returncode != 0 or not common_dir:
        return {
            **base,
            "status": "retained",
            "reason": "git_common_dir_unresolved",
            "branch": branch,
            "event_type": "autoresearch.worktree.retained",
        }
    removed = subprocess.run(
        ["git", "--git-dir", common_dir, "worktree", "remove", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if removed.returncode != 0:
        return {
            **base,
            "status": "retained",
            "reason": "clean_worktree_remove_failed",
            "branch": branch,
            "stderr_tail": (removed.stderr or "")[-1000:],
            "event_type": "autoresearch.worktree.retained",
        }

    branch_deleted = False
    if branch:
        deleted = subprocess.run(
            ["git", "--git-dir", common_dir, "branch", "-d", branch],
            capture_output=True,
            text=True,
            check=False,
        )
        branch_deleted = deleted.returncode == 0
    return {
        **base,
        "status": "cleaned",
        "reason": "terminal_worktree_clean",
        "branch": branch,
        "branch_deleted": branch_deleted,
        "event_type": "autoresearch.worktree.cleaned",
    }
