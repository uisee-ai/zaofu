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
_WRAPPERS = {"env", "command", "exec", "sudo", "nohup", "timeout", "time", "busybox", "xargs"}
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}


def _has_copy_command(command: str, depth: int = 0) -> bool:
    """Recognize command positions without treating quoted arguments as code.

    This only selects conservative rejection, never authorizes execution.
    Known wrappers remain unproven; ordinary command arguments stay data.
    """
    if depth >= 4:
        return bool(re.search(r"\bcp\b", command))
    lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|(){}<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens: list[str] = []
    try:
        tokens.extend(lexer)
    except ValueError:
        pass  # A partial leading cp still needs conservative rejection.
    head, wrapper, shell, shell_body = True, False, False, False
    uncertain_wrapper = False
    previous = ""
    for raw in tokens:
        if raw and set(raw) <= set(";&|\n"):
            head, wrapper, shell, shell_body = True, False, False, False
            uncertain_wrapper = False
            previous = raw
            continue
        if raw == "(" and (head or previous.endswith("$") or previous in {"<", ">"}):
            head, wrapper, shell, shell_body = True, False, False, False
            uncertain_wrapper = False
            previous = raw
            continue
        if head and raw in {"(", ")", "{", "}", "!", "if", "then", "else", "do", "while", "until"}:
            continue
        try:
            decoded = shlex.split(raw)
        except ValueError:
            decoded = []
        word = decoded[0] if len(decoded) == 1 else raw
        name = word.rsplit("/", 1)[-1]
        if shell_body:
            if _has_copy_command(word, depth + 1):
                return True
            shell_body = False
        elif shell:
            shell_body = word.startswith("-") and "c" in word[1:]
        elif head or wrapper:
            if name == "cp":
                return True
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
                continue
            if name in _WRAPPERS:
                wrapper = True
                uncertain_wrapper = uncertain_wrapper or name == "timeout"
            elif name in _SHELLS:
                shell, wrapper = True, False
            elif wrapper and word.startswith("-"):
                uncertain_wrapper = uncertain_wrapper or word != "--"
            elif not uncertain_wrapper:
                wrapper = False
            head = False
        previous = raw
    return False


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
    match = _LITERAL_COPY.fullmatch(command)
    if match is None:
        return CopyIntent() if _has_copy_command(command) else None
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
