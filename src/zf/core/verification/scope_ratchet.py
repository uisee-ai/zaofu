"""Scope ratchet — detect unauthorized file changes per turn."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path


@dataclass
class ScopeViolation:
    path: str
    reason: str  # not_in_allowed, in_blocked


@dataclass
class ScopeSnapshot:
    """Snapshot of file modification times at turn start."""
    files: dict[str, float]  # relative_path -> mtime


class ScopeRatchet:
    """Detect out-of-scope file modifications.

    ``ignore_prefixes`` paths are excluded from snapshots entirely
    (typical use: skip ``.zf/``, ``.git/``, ``__pycache__/`` so
    zaofu's own state files aren't reported as agent activity).
    Default is empty for backward compatibility — callers wanting
    standard ignores must pass them explicitly.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        ignore_prefixes: list[str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.ignore_prefixes = list(ignore_prefixes or [])

    def _is_ignored(self, rel_path: str) -> bool:
        for prefix in self.ignore_prefixes:
            if rel_path == prefix or rel_path.startswith(prefix + "/"):
                return True
        return False

    def snapshot(self) -> ScopeSnapshot:
        """Capture current file tree state, skipping ignored prefixes."""
        files: dict[str, float] = {}
        for path in self.workspace.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.workspace))
            if self._is_ignored(rel):
                continue
            try:
                files[rel] = path.stat().st_mtime
            except OSError:
                continue
        return ScopeSnapshot(files=files)

    def diff(self, before: ScopeSnapshot, after: ScopeSnapshot) -> list[str]:
        """Compute changed files between two snapshots."""
        changed: list[str] = []
        for path, mtime in after.files.items():
            if path not in before.files or before.files[path] != mtime:
                changed.append(path)
        # New files
        for path in after.files:
            if path not in before.files:
                if path not in changed:
                    changed.append(path)
        return changed

    def check(
        self,
        changed_files: list[str],
        allowed: list[str],
        blocked: list[str],
    ) -> list[ScopeViolation]:
        """Check changed files against scope constraints."""
        violations: list[ScopeViolation] = []
        for f in changed_files:
            # Check blocked first
            for pattern in blocked:
                if f.startswith(pattern) or fnmatch(f, pattern):
                    violations.append(ScopeViolation(path=f, reason="in_blocked"))
                    break
            else:
                # Check allowed
                if allowed:
                    in_allowed = any(
                        f.startswith(pattern) or fnmatch(f, pattern)
                        for pattern in allowed
                    )
                    if not in_allowed:
                        violations.append(ScopeViolation(path=f, reason="not_in_allowed"))
        return violations
