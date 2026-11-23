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
from zf.cli.hook_copy_intent import copy_intent, copy_paths_are_proven, resolved_copy_target


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
    from zf.cli.literal_python_read import literal_python_read_query

    if _literal_shell_patch(command) is not None:
        return True
    if _literal_controlled_result_stdin(command):
        return False
    if literal_python_read_query(command):
        return False
    normalized = f" {_NON_MUTATING_REDIRECTION_RE.sub(' ', command).strip()} "
    if " zf task-doc ingest " in normalized:
        return True
    lexer = shlex.shlex(normalized, posix=True, punctuation_chars="<>|&;")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        # Fail closed for malformed shell input with active side-effect syntax.
        return (
            copy_intent(command) is not None
            or _contains_active_shell_substitution(command)
            or bool(re.search(r"(?:^|\s)(?:\d*)>>?(?:\s|[^=])", normalized))
        )
    if _is_simple_controlled_zf_result_command(command, tokens):
        return False
    if copy_intent(command) is not None:
        return True
    if _contains_active_shell_substitution(command):
        return True
    if any(marker in normalized for marker in _BASH_MUTATING_MARKERS):
        return True
    return any(token in {">", ">>", ">|", "&>", "&>>"} for token in tokens)


def _contains_active_shell_substitution(command: str) -> bool:
    """Return whether shell substitution syntax appears outside single quotes."""

    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == "'":
                quote = ""
            index += 1
            continue
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "'" and not quote:
            quote = "'"
            index += 1
            continue
        if char == '"':
            if quote == '"':
                quote = ""
            elif not quote:
                quote = '"'
            index += 1
            continue
        if char == "`" or command.startswith("$(", index):
            return True
        if not quote and (
            command.startswith("<(", index)
            or command.startswith(">(", index)
        ):
            return True
        index += 1
    return False


def _is_simple_controlled_zf_result_command(
    command: str,
    tokens: list[str],
    *,
    stdin_only: bool = False,
) -> bool:
    """Recognize one side-effect-controlled zf CLI invocation.

    Semantic JSON is opaque data. Looking for strings such as ``.write_text``
    inside a quoted result or assistance payload incorrectly
    classifies that data as a Python filesystem mutation. Shell composition
    remains fail-closed: substitutions, pipes, separators and redirects never
    enter this exemption.
    """

    if _contains_active_shell_substitution(command):
        return False
    shell_operators = {
        ";", "|", "||", "&", "&&", ">", ">>", ">|", "&>", "&>>", "<", "<<",
    }
    if any(token in shell_operators for token in tokens):
        return False
    index = 0
    while index < len(tokens) and re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*=.*",
        tokens[index],
    ):
        index += 1
    if index < len(tokens) and tokens[index].rsplit("/", 1)[-1] == "env":
        index += 1
        while index < len(tokens) and (
            tokens[index].startswith("-")
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index])
        ):
            index += 1
    if index + 2 < len(tokens) and (
        tokens[index].rsplit("/", 1)[-1] == "uv"
        and tokens[index + 1] == "run"
    ):
        index += 2
    if index >= len(tokens) or tokens[index].rsplit("/", 1)[-1] not in {
        "zf",
        "zf-cli",
    }:
        return False
    args = tokens[index + 1:]
    if stdin_only:
        return args[:2] == ["result", "validate"] and "--stdin" in args
    return bool(
        args[:1] == ["emit"]
        or args[:2] in (["result", "submit"], ["result", "validate"])
        or args[:3] == ["recover", "assistance", "request"]
    )


def _literal_controlled_result_stdin(command: str) -> bool:
    """Recognize only one controlled CLI with a fully literal stdin heredoc.

    The first closing delimiter must end the command. Shell syntax in the
    header keeps the normal conservative checks; the body reaches the existing
    result authorization and schema validators as data, not executable shell.
    """
    lines = command.strip().splitlines()
    if not lines:
        return False
    header = re.fullmatch(
        r"(.+?)\s+<<\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2", lines[0],
    )
    if header is None:
        return False
    end = next((i for i, line in enumerate(lines[1:], 1) if line == header.group(3)), None)
    if end != len(lines) - 1:
        return False
    prefix = header.group(1)
    lexer = shlex.shlex(prefix, posix=True, punctuation_chars="<>|&;")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    return _is_simple_controlled_zf_result_command(prefix, tokens, stdin_only=True)


def _literal_shell_patch(command: str) -> str | None:
    """Recognize only one apply_patch with an entirely literal heredoc body.

    Unsupported shell syntax retains conservative command inspection. The first
    delimiter must end the command: a later delimiter cannot hide shell suffixes.
    """
    lines = command.strip().splitlines()
    if not lines:
        return None
    header = re.fullmatch(r"apply_patch\s+<<\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1", lines[0])
    if header is None:
        return None
    delimiter = header.group(2)
    end = next((i for i, line in enumerate(lines[1:], 1) if line == delimiter), None)
    if end != len(lines) - 1:
        return None
    body = lines[1:end]
    if len(body) < 3 or body[0] != "*** Begin Patch" or body[-1] != "*** End Patch":
        return None
    return "\n".join(body)


def write_target_paths(tool_name: str, tool_input: dict) -> list[str]:
    """Return explicit paths targeted by provider-native write tools."""
    # Parent directory creation is checked by the workdir guard, not file globs.
    return [path for operation, path in write_target_operations(tool_name, tool_input) if operation != "mkdir"]


def runtime_write_guard_text(tool_name: str, tool_input: object, *, cwd: str = "") -> str:
    """Inspect explicit file targets, retaining conservative unknown-input checks."""
    targets = (
        [path for _, path in write_target_operations(tool_name, tool_input, cwd=cwd)]
        if isinstance(tool_input, dict) else []
    )
    if targets:
        # Quoted ledger paths in a plan are data, not mutation targets.
        return "\n".join(targets)
    return (
        json.dumps(tool_input, ensure_ascii=False)
        if isinstance(tool_input, (dict, list))
        else str(tool_input or "")
    )


def write_target_operations(
    tool_name: str,
    tool_input: dict,
    *,
    cwd: str = "",
) -> list[tuple[str, str]]:
    """Return targets for native writes and single literal shell patches."""

    lower = tool_name.lower().strip()
    if lower in {"bash", "shell"}:
        from zf.cli.literal_shell_write import literal_shell_write_targets

        command = str(tool_input.get("command") or "")
        targets = literal_shell_write_targets(command)
        if targets:
            return targets
        target = _literal_handoff_target(command)
        if target:
            return [("write", target)]
        copy = copy_intent(command)
        if copy and copy.target:
            return [("copy", resolved_copy_target(copy, cwd))]
    if lower in {"write", "edit", "multiedit", "notebookedit"}:
        raw = (
            tool_input.get("file_path")
            or tool_input.get("path")
            or tool_input.get("notebook_path")
        )
        text = str(raw or "").strip()
        return [(lower, text)] if text else []
    if lower not in {"apply_patch", "bash", "shell"}:
        return []
    command = str(
        tool_input.get("command")
        or tool_input.get("patch")
        or tool_input.get("input")
        or ""
    )
    if lower in {"bash", "shell"}:
        patch = _literal_shell_patch(str(tool_input.get("command") or ""))
        if patch is None:
            return []
        command = patch
    operations: list[tuple[str, str]] = []
    for line in command.splitlines():
        stripped = line.strip()
        for operation, marker in (
            ("add", "*** Add File:"),
            ("update", "*** Update File:"),
            ("delete", "*** Delete File:"),
            ("move", "*** Move to:"),
        ):
            if stripped.startswith(marker):
                target = stripped[len(marker):].strip()
                if target:
                    operations.append((operation, target))
    return operations


def _literal_handoff_target(command: str) -> str:
    """Recognize one literal cat heredoc; everything else keeps shell guards.

    Only a quoted delimiter and a plain literal target are accepted. The full
    command must end at its first delimiter: bodies may contain inert code, but
    commands after an early delimiter must never be hidden as document text.
    """
    header, newline, rest = command.partition("\n")
    if not newline:
        return ""
    match = re.fullmatch(
        r"[ \t]*cat[ \t]+>>?[ \t]*"
        r"(?P<target>[A-Za-z0-9_./-]+|'[A-Za-z0-9_./ -]+'|\"[A-Za-z0-9_./ -]+\")"
        r"[ \t]+<<[ \t]*(?P<quote>['\"])(?P<end>[A-Za-z_][A-Za-z_0-9]*)"
        r"(?P=quote)[ \t]*",
        header,
    )
    if match is None:
        return ""
    lines = rest.removesuffix("\n").split("\n")
    delimiter = match.group("end")
    if lines[-1] != delimiter or delimiter in lines[:-1]:
        return ""
    return shlex.split(match.group("target"))[0]


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


def _absolute_root_path_mentions(command: str, root: Path) -> list[Path]:
    """Extract explicit paths rooted at ``root`` from a shell command.

    This extends the legacy string heuristic, not a shell/Python interpreter.
    Every root occurrence must form a complete simple path; never silently
    ignore a bare root or accept the authorized prefix of a longer expression.
    """

    root_text = str(root.resolve(strict=False)).rstrip("/")
    if not root_text:
        return []
    pattern = re.compile(
        re.escape(root_text) + r"(?:/[A-Za-z0-9_.-]+)+"
    )
    paths: list[Path] = []
    for occurrence in re.finditer(re.escape(root_text), command):
        match = pattern.match(command, occurrence.start())
        if match is None or (
            match.end() < len(command)
            and command[match.end()] not in "\"' \t\r\n"
        ):
            return []
        path = Path(match.group())
        if path not in paths:
            paths.append(path)
    return paths


def _command_uses_only_authorized_result_scratch(
    *,
    command: str,
    project_root: Path,
    state_dir: Path,
    event_log: EventLog,
    actor: str,
) -> bool:
    """Allow a compound writer command only for its exact signed scratch path."""

    mentions = _absolute_root_path_mentions(command, project_root)
    return bool(mentions) and all(
        is_authorized_result_scratch_target(
            state_dir,
            event_log,
            actor=actor,
            target=path,
        )
        for path in mentions
    )


def is_authorized_result_scratch_target(
    state_dir: Path,
    event_log: EventLog,
    *,
    actor: str,
    target: str | Path,
    cwd: str | Path = "",
) -> bool:
    """Resolve a provider target before checking its exact scratch capability."""
    path = Path(target).expanduser()
    if not path.is_absolute():
        if not str(cwd).strip():
            return False
        path = Path(cwd) / path
    from zf.runtime.result_submit import is_authorized_result_scratch_write

    if is_authorized_result_scratch_write(
        state_dir,
        event_log,
        role_instance=actor,
        target=path,
    ):
        return True
    from zf.runtime.fanout_result_draft import is_authorized_fanout_result_draft

    return is_authorized_fanout_result_draft(
        state_dir, event_log, role_instance=actor, target=path,
    )


def _writer_workdir_binding(
    state_dir: Path, events: list[ZfEvent], actor: str,
) -> tuple[Path, bool, bool]:
    """Share the existing kernel assignment rules with contract scope framing."""
    from zf.cli.hook_dispatch_context import active_task_pipeline_dispatch

    pipeline = active_task_pipeline_dispatch(events, actor)
    has_pipeline = any(
        event.type == "task.pipeline.stage.dispatched"
        and event.payload.get("role_instance") == actor for event in events
    )
    root = state_dir / "workdirs" / actor / "project"
    if pipeline is not None:
        workdir = str(pipeline.payload.get("workdir") or "")
        if workdir and Path(workdir).is_absolute():
            root = Path(workdir)
    unresolved = False
    if pipeline is None and has_pipeline:
        from zf.runtime.workflow_operation import reduce_workflow_operations

        unresolved = any(
            row.get("status") == "running"
            and row.get("role_instance") == actor
            and row.get("task_pipeline_stage")
            for row in reduce_workflow_operations(events).values()
        )
    return root, has_pipeline, unresolved


def workdir_scope_frames(
    state_dir: Path, event_log: EventLog, actor: str, targets: list[str], cwd: str,
) -> dict[str, str | None]:
    """Resolve one binding for every target; cwd cannot grant another root."""
    from zf.cli.hook_context_events import hook_context_events

    root, _, unresolved = _writer_workdir_binding(
        state_dir, hook_context_events(event_log), actor,
    )
    frames: dict[str, str | None] = dict.fromkeys(targets)
    if not unresolved:
        for target in frames:
            path = Path(cwd or root) / target
            try:
                frames[target] = path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
            except (OSError, RuntimeError, ValueError):
                pass
    return frames


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
    tool_input = event_payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    tool_name = str(event_payload.get("tool_name") or "")
    targets = write_target_operations(
        tool_name, tool_input, cwd=str(event_payload.get("cwd") or "")
    )
    command = str(tool_input.get("command") or "") if tool_name.lower() in {"bash", "shell"} else ""
    copy = copy_intent(command) if not targets or targets[0][0] == "copy" else None
    mutating_shell = bool(command and bash_command_looks_mutating(command))
    if not targets and not mutating_shell:
        return 0
    from zf.cli.hook_context_events import hook_context_events

    events = hook_context_events(event_log)
    assigned_root, has_pipeline_assignment, unresolved_pipeline = _writer_workdir_binding(
        state_dir, events, actor,
    )
    if not has_pipeline_assignment and not assigned_root.is_dir():
        return 0
    cwd = Path(str(event_payload.get("cwd") or assigned_root))
    task_id = active_task_id_for_actor(event_log, actor)
    allowed_workdirs = set() if unresolved_pipeline else {assigned_root}
    project_roots = {Path(project_root), state_dir.parent}
    offending: list[str] = []
    unproven_copy = copy is not None and not copy_paths_are_proven(copy, cwd)
    if unproven_copy:
        offending.append(copy.target or "<unproven cp command>")
    for operation, raw_target in targets:
        target = Path(raw_target)
        target = target if target.is_absolute() else cwd / target
        if (
            (
                operation == "copy"
                or tool_name.lower() in {"bash", "shell"}
                or any(_path_is_within(target, root) for root in project_roots)
            )
            and not any(
                _path_is_within(target, root) for root in allowed_workdirs
            )
        ):
            if is_authorized_result_scratch_target(
                state_dir,
                event_log,
                actor=actor,
                target=target,
            ) and operation in {"update", "write", "edit", "multiedit", "notebookedit"}:
                continue
            offending.append(str(target))
    if tool_name.lower() in {"bash", "shell"} and not targets:
        for project_path in project_roots:
            if (
                mutating_shell
                and str(project_path.resolve(strict=False)) in command
                and not any(
                    str(root.resolve(strict=False)) in command
                    for root in allowed_workdirs
                )
                and not _command_uses_only_authorized_result_scratch(
                    command=command,
                    project_root=project_path,
                    state_dir=state_dir,
                    event_log=event_log,
                    actor=actor,
                )
            ):
                offending.append(str(project_path.resolve(strict=False)))
    if not offending:
        return 0
    event_writer.append(ZfEvent(
        type="worker.scope_write.rejected",
        actor="zf-cli",
        task_id=task_id or None,
        payload={
            "reason": "unproven_copy_write_target" if unproven_copy else "outside_assigned_workdir",
            "failure_class": (
                "workspace_binding_mismatch" if unresolved_pipeline else "scope_violation"
            ),
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
            "assigned_workdir": "" if unresolved_pipeline else str(assigned_root),
        },
        causation_id=causation_id,
    ))
    print(
        ("ZaoFu blocked this write: cp requires one literal regular source and a non-linked file destination; "
         "unsupported options/composition stay blocked. Read the source then use a relative patch. "
         if unproven_copy else "ZaoFu blocked this write: ")
        + "use the assigned writer workdir only: "
        + ("unresolved task pipeline assignment" if unresolved_pipeline else str(assigned_root))
        + ". The only state-dir exception is the exact current result scratch "
        "declared by the Kernel. For handoff artifacts, write a workdir-relative "
        "file and emit that ref; the kernel relocates it into runtime artifact storage.",
        file=sys.stderr,
    )
    return 2
