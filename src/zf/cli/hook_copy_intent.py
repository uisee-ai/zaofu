"""A bounded copy grammar, not a shell interpreter or filesystem sandbox."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path


_PATH = r'''(?:[A-Za-z0-9_./-]+|'[A-Za-z0-9_./ -]+'|"[A-Za-z0-9_./ -]+")'''
_LITERAL_COPY = re.compile(
    rf"[ \t]*cp[ \t]+(?P<end>--[ \t]+)?(?P<source>{_PATH})"
    rf"[ \t]+(?P<target>{_PATH})[ \t]*"
)
_COPY_WORD = re.compile(r'''(?:^|[\s;'"|&()])(?:/[A-Za-z0-9_./-]+/)?cp(?=$|[\s;'"|&()])''')


@dataclass(frozen=True)
class CopyIntent:
    source: str = ""
    target: str = ""


def copy_intent(command: str) -> CopyIntent | None:
    """None is unrelated; empty fields mean a recognized but unproven copy.

    Wrappers, options, multiple commands, expansions and malformed quoting do
    not acquire a write exemption. Token inspection only detects cp; the full
    literal grammar alone can establish its operands.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    if not _COPY_WORD.search(command) and not any(
        token.rsplit("/", 1)[-1] == "cp" for token in tokens
    ):
        return None
    match = _LITERAL_COPY.fullmatch(command)
    if match is None:
        return CopyIntent()
    source = shlex.split(match["source"])[0]
    target = shlex.split(match["target"])[0]
    if not match["end"] and (source.startswith("-") or target.startswith("-")):
        return CopyIntent()
    return CopyIntent(source, target)


def resolved_copy_target(intent: CopyIntent, cwd: str | Path = "") -> str:
    """Use the same destination in runtime, workspace and task-scope guards."""
    if not intent.target or not str(cwd):
        return intent.target
    try:
        return str((Path(cwd) / intent.target).resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return intent.target  # Workdir validation rejects unresolvable paths.


def copy_paths_are_proven(intent: CopyIntent, cwd: Path) -> bool:
    """Only regular-file copies to an explicit, non-symlink file destination.

    Directory destinations change cp's actual leaf name. Linked destinations
    can write another scope/inode. Reject these forms instead of guessing.
    This preflight does not claim protection against concurrent path changes.
    """
    if not intent.source or not intent.target or not cwd.is_absolute():
        return False
    source, target = cwd / intent.source, cwd / intent.target
    try:
        if not source.is_file():
            return False
        if any(path.is_symlink() for path in (target, *target.parents)):
            return False
        target.resolve(strict=False)
        if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
            return False
    except (OSError, RuntimeError, ValueError):
        return False
    return True
