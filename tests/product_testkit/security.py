"""Product-agnostic security and untrusted-input assertions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^\s'\"]{8,}")


def assert_path_inside(path: Path, *, root: Path) -> None:
    candidate = path.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise AssertionError(f"path escapes product root: {path}") from exc


def find_secret_like_text(paths: Iterable[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _SECRET.search(text):
            findings.append(str(path))
    return findings


def assert_no_secret_leaks(paths: Iterable[Path]) -> None:
    findings = find_secret_like_text(paths)
    assert not findings, f"secret-like content found in: {findings}"

