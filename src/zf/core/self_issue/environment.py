"""Project-neutral environment and version detection for Issue Drafts.

The detector is deliberately independent of ZaoFu configuration and package
metadata.  A host may replace it, but the default works for common Python,
Node, and plain source repositories without importing the target project.
"""

from __future__ import annotations

import json
import platform
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def detect_environment() -> dict[str, str]:
    """Return bounded, user-editable host facts without probing a runtime."""
    return {
        "os": platform.system() or "unknown",
        "version": platform.release() or "unknown",
        "arch": platform.machine() or "unknown",
        "source": "host_detector",
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }


def detect_project_version(project_root: Path) -> dict[str, str]:
    """Detect a project version from repository-owned metadata.

    No project code is imported or executed.  A short Git revision is used as
    the final repository-local fallback; a completely unversioned directory
    reports ``unknown`` instead of assuming a host product version.
    """
    root = Path(project_root).resolve(strict=False)
    candidates = (
        _pyproject_version(root / "pyproject.toml"),
        _package_json_version(root / "package.json"),
        *(_text_version(root / name) for name in ("VERSION", "VERSION.txt", ".version")),
        _git_version(root),
    )
    for value, source, confidence in candidates:
        if value:
            return {"value": value[:120], "source": source, "confidence": confidence}
    return {"value": "unknown", "source": "unknown", "confidence": "low"}


def _pyproject_version(path: Path) -> tuple[str, str, str]:
    try:
        value: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "", "", ""
    project = value.get("project") if isinstance(value.get("project"), dict) else {}
    version = str(project.get("version") or "").strip()
    if not version:
        tool = value.get("tool") if isinstance(value.get("tool"), dict) else {}
        poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
        version = str(poetry.get("version") or "").strip()
    return (version, "pyproject.toml", "high") if version else ("", "", "")


def _package_json_version(path: Path) -> tuple[str, str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", ""
    version = str(value.get("version") or "").strip() if isinstance(value, dict) else ""
    return (version, "package.json", "high") if version else ("", "", "")


def _text_version(path: Path) -> tuple[str, str, str]:
    try:
        version = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return "", "", ""
    return (version, path.name, "medium") if version else ("", "", "")


def _git_version(root: Path) -> tuple[str, str, str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", "", ""
    version = result.stdout.strip() if result.returncode == 0 else ""
    return (version, "git", "medium") if version else ("", "", "")
