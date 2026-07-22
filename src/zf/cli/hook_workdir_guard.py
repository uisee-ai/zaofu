"""Provider-hook policy for keeping writer tools inside assigned workdirs."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


_BASH_MUTATING_MARKERS = (
    " tee ", " sed -i", " perl -i", " rm ", " mv ",
    " cp ", " truncate ", ".write_text", ".write_bytes",
)
_NON_MUTATING_REDIRECTION_RE = re.compile(
    r"(?<!\S)(?:[012]?>&[012]|[012]?>\s*/dev/null)(?=\s|[;&|)]|$)"
)


def actor_from_workdir_cwd(state_dir: Path, payload: dict) -> str:
    raw_cwd = str(payload.get("cwd") or "").strip()
    if not raw_cwd:
        return ""
    cwd = Path(raw_cwd)
    if not cwd.is_absolute():
        return ""
    state_root = state_dir if state_dir.is_absolute() else Path.cwd() / state_dir
    try:
        relative = cwd.resolve(strict=False).relative_to(
            (state_root / "workdirs").resolve(strict=False)
        )
    except (OSError, ValueError):
        return ""
    parts = relative.parts
    if len(parts) < 2 or parts[1] != "project":
        return ""
    return parts[0] if parts[0] not in {"", ".", ".."} else ""


def bash_command_looks_mutating(command: str) -> bool:
    normalized = f" {_NON_MUTATING_REDIRECTION_RE.sub(' ', command).strip()} "
    if " zf task-doc ingest " in normalized:
        return True
    if any(marker in normalized for marker in _BASH_MUTATING_MARKERS):
        return True
    lexer = shlex.shlex(normalized, posix=True, punctuation_chars="<>|&;")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        # Fail closed for malformed shell input that still visibly redirects.
        return bool(re.search(r"(?:^|\s)(?:\d*)>>?(?:\s|[^=])", normalized))
    return any(token in {">", ">>", ">|", "&>", "&>>"} for token in tokens)


def write_target_paths(tool_name: str, tool_input: dict) -> list[str]:
    """Return explicit paths targeted by provider-native write tools."""
    lower = tool_name.lower().strip()
    if lower in {"write", "edit", "multiedit", "notebookedit"}:
        raw = (
            tool_input.get("file_path")
            or tool_input.get("path")
            or tool_input.get("notebook_path")
        )
        text = str(raw or "").strip()
        return [text] if text else []
    if lower != "apply_patch":
        return []
    command = str(
        tool_input.get("command")
        or tool_input.get("patch")
        or tool_input.get("input")
        or ""
    )
    paths: list[str] = []
    for line in command.splitlines():
        stripped = line.strip()
        for marker in (
            "*** Add File:",
            "*** Update File:",
            "*** Delete File:",
            "*** Move to:",
        ):
            if stripped.startswith(marker):
                target = stripped[len(marker):].strip()
                if target:
                    paths.append(target)
    return paths


def tool_input_digest(tool_input: dict) -> str:
    raw = json.dumps(
        tool_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return True


def evaluate_workdir_write_guard(
    *,
    state_dir: Path,
    project_root: Path,
    event_type: str,
    actor: str,
    event_payload: dict,
    event_log: EventLog,
    event_writer: EventWriter,
    causation_id: str | None,
    should_check_actor: Callable[[str], bool],
    active_task_id_for_actor: Callable[[EventLog, str], str],
) -> int:
    if not event_type.endswith(".pre_tool_use") or not should_check_actor(actor):
        return 0
    assigned_root = state_dir / "workdirs" / actor / "project"
    if not assigned_root.is_dir():
        return 0
    tool_input = event_payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    tool_name = str(event_payload.get("tool_name") or "")
    cwd = Path(str(event_payload.get("cwd") or assigned_root))
    project_roots = {Path(project_root), state_dir.parent}
    offending: list[str] = []
    for raw_target in write_target_paths(tool_name, tool_input):
        target = Path(raw_target)
        target = target if target.is_absolute() else cwd / target
        if (
            any(_path_is_within(target, root) for root in project_roots)
            and not _path_is_within(target, assigned_root)
        ):
            offending.append(str(target))
    if tool_name.lower() in {"bash", "shell"}:
        command = str(tool_input.get("command") or "")
        if (
            bash_command_looks_mutating(command)
            and str(project_root.resolve(strict=False)) in command
            and str(assigned_root.resolve(strict=False)) not in command
        ):
            offending.append(str(project_root.resolve(strict=False)))
    if not offending:
        return 0
    task_id = active_task_id_for_actor(event_log, actor)
    event_writer.append(ZfEvent(
        type="worker.scope_write.rejected",
        actor="zf-cli",
        task_id=task_id or None,
        payload={
            "reason": "outside_assigned_workdir",
            "worker": actor,
            "origin_event": event_type,
            "tool_name": tool_name,
            "tool_input_digest": tool_input_digest(tool_input),
            "command_class": (
                "mutating_shell"
                if tool_name.lower() in {"bash", "shell"}
                else "provider_write"
            ),
            "offending_paths": sorted(set(offending))[:10],
            "assigned_workdir": str(assigned_root),
        },
        causation_id=causation_id,
    ))
    print(
        "ZaoFu blocked this write: use the assigned writer workdir only: "
        + str(assigned_root)
        + ". For handoff artifacts, write a workdir-relative file and emit "
        "that ref; the kernel relocates it into runtime artifact storage.",
        file=sys.stderr,
    )
    return 2
