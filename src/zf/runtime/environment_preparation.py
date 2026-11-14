"""Preparation bookkeeping keyed by physical workspace, independent of role slots.

This installer mutex is not an execution-holder lease. It does not authorize
rebuilding an environment still in use by a worker.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


def environment_preparation_dir(worktree: Path) -> Path:
    physical = worktree.resolve()
    try:
        result = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=physical,
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError("environment preparation identity unavailable") from exc
    if result.returncode == 0:
        key = hashlib.sha256(str(physical).encode()).hexdigest()[:24]
        return Path(result.stdout.strip()) / "zf-environment" / key
    # Non-Git fixture/product directories have no separate Git metadata area.
    return physical / ".zf-setup-state"


def declared_setup_identity(worktree: Path, paths: list[str]) -> str:
    """Hash declared tool/data identity files without loading their bodies into memory."""
    root = worktree.resolve()
    digest = hashlib.sha256()
    for name in sorted(set(paths)):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("setup input must be workspace relative")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"declared setup input unavailable: {name}")
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(chunk)
        digest.update(name.encode() + b"\0" + file_digest.digest())
    return digest.hexdigest()
