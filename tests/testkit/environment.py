"""Immutable run-environment metadata for ZaoFu TestKit evidence."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from .evidence import sha256_file, write_json_atomic


def _git(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def capture_environment(
    project_root: Path,
    state_dir: Path,
    *,
    config_path: Path | None = None,
    lock_path: Path | None = None,
) -> dict[str, Any]:
    """Capture stable identity without writing into the project's state root."""
    root = project_root.resolve()
    state = state_dir.resolve()
    metadata: dict[str, Any] = {
        "schema_version": "zf-testkit-environment.v1",
        "project_root": str(root),
        "state_dir": str(state),
        "git_head": _git(root, "rev-parse", "HEAD"),
        "git_dirty": bool(_git(root, "status", "--porcelain")),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for key, path in (("config_digest", config_path), ("lock_digest", lock_path)):
        if path is not None and path.exists():
            metadata[key] = sha256_file(path.resolve())
    return metadata


def write_environment(path: Path, metadata: dict[str, Any]) -> str:
    """Persist the captured environment as an atomic evidence sidecar."""
    return write_json_atomic(path, metadata)
