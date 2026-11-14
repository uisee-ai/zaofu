"""Mechanical preservation checks shared by task, role and stop cleanup."""
from pathlib import Path
import subprocess
from typing import Iterable

from zf.core.safety.path_guard import WORKDIR_OWNER_MARKER
from zf.runtime.worktree_env import SETUP_MARKER


def workspace_retention_reason(
    project_path: Path, *, provision_paths: Iterable[str] = (),
    bookkeeping_dir: Path | None = None,
) -> str:
    """Unknown ignored outputs are retained; declared dependencies are caches.

    This is an additional guard, not proof of released users or integrated code.
    Git's non-force removal still checks tracked and untracked changes later.
    """
    if bookkeeping_dir is not None:
        owned = {"project", "meta.json", WORKDIR_OWNER_MARKER, SETUP_MARKER, SETUP_MARKER + ".lock"}
        if any(entry.name not in owned for entry in bookkeeping_dir.iterdir()):
            return "workspace contains material outside project requiring archival"
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
            cwd=project_path, capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "ignored workspace inventory is unavailable"
    if result.returncode:
        return "ignored workspace inventory failed"
    dependencies = {".venv", "node_modules"}.intersection(provision_paths)
    caches = dependencies | {".pytest_cache", ".ruff_cache", ".mypy_cache"}
    for name in result.stdout.split("\0"):
        if not name:
            continue
        parts = Path(name).parts
        if parts[0] in caches or ("__pycache__" in parts and name.endswith((".pyc", ".pyo"))):
            continue
        return "ignored workspace files require archival before cleanup"
    return ""
