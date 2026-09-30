"""Portable, read-only investigation tools for the Issue Agent."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence


class ReadOnlyInvestigationTools:
    def __init__(self, root: Path, *, allowed_paths: Sequence[str] = (".",)) -> None:
        self.root = Path(root).resolve()
        self.allowed_paths = tuple(str(item).strip("/") or "." for item in allowed_paths)

    def _resolve(self, value: str) -> Path:
        candidate = (self.root / value).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PermissionError("path escapes investigation root")
        relative = candidate.relative_to(self.root).as_posix() or "."
        if not any(
            allowed == "." or relative == allowed or relative.startswith(allowed + "/")
            for allowed in self.allowed_paths
        ):
            raise PermissionError("path is outside allowed investigation paths")
        return candidate

    def read_text(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return target.read_text(encoding="utf-8", errors="replace")

    def search(self, pattern: str, *, path: str = ".") -> list[str]:
        base = self._resolve(path)
        if not base.exists():
            return []
        matches: list[str] = []
        for candidate in base.rglob("*") if base.is_dir() else (base,):
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pattern in text:
                matches.append(candidate.relative_to(self.root).as_posix())
        return matches

    def run_read_only(self, command: Sequence[str], *, timeout_seconds: int) -> Mapping[str, Any]:
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError("read-only command must be a non-empty string sequence")
        completed = subprocess.run(
            list(command),
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
        return {
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
        }


__all__ = ["ReadOnlyInvestigationTools"]
