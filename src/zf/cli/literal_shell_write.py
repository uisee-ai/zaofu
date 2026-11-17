"""Recognize one literal cat heredoc, never an executed script or shell program."""
from __future__ import annotations

import re
import shlex


def literal_shell_write_targets(command: str) -> list[tuple[str, str]]:
    lines = command.splitlines()
    if not lines:
        return []
    operations: list[tuple[str, str]] = []
    # The observed writer creates its relative parents first. Accept only this
    # narrow prefix; separators, options, expansions and other commands fail closed.
    if lines[0].startswith("mkdir -p "):
        try:
            parents = shlex.split(lines.pop(0))[2:]
        except ValueError:
            return []
        if not parents or any(not _literal_path(path) or path.startswith("-") for path in parents):
            return []
        operations.extend(("mkdir", path) for path in parents)
    if not lines:
        return []
    match = re.fullmatch(r"cat\s+>\s*(.+?)\s*<<(['\"])([A-Za-z_][A-Za-z0-9_]*)\2\s*", lines[0])
    if not match:
        return []
    try:
        target = shlex.split(match[1])
    except ValueError:
        return []
    if len(target) != 1 or not _literal_path(target[0]):
        return []
    end = next((index for index, line in enumerate(lines[1:], 1) if line == match[3]), None)
    if end is None or any(line.strip() for line in lines[end + 1:]):
        return []
    return [*operations, ("write", target[0])]


def _literal_path(path: str) -> bool:
    return bool(path) and not any(char in path for char in "$`;&|()<>\\*?[]{}~\r\n")
