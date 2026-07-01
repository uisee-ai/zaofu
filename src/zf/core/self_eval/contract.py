"""YAML contract loader for deterministic self-eval runs."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_VALID_METRIC_DIRECTIONS = frozenset({
    "higher_is_better",
    "lower_is_better",
})
# Provider names below are command/module denylist labels only; this module
# does not import or execute any Anthropic/OpenAI/Codex provider SDK or CLI.
_PROVIDER_CLI_EXECUTABLES = frozenset({
    "anthropic",
    "claude",
    "claude-code",
    "codex",
    "openai",
})
_WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".ps1", ".com")
_PROVIDER_CLI_EXECUTABLE_PATTERN = "|".join(
    re.escape(executable)
    for executable in sorted(_PROVIDER_CLI_EXECUTABLES, key=len, reverse=True)
)
_WINDOWS_EXECUTABLE_SUFFIX_PATTERN = "|".join(
    re.escape(suffix) for suffix in _WINDOWS_EXECUTABLE_SUFFIXES
)
_WINDOWS_PROVIDER_EXECUTABLE_RE = re.compile(
    rf"(?i)(?:^|[\s\"'])(?:[A-Za-z]:\\|\\\\)[^;&|\"']*\\"
    rf"(?P<name>{_PROVIDER_CLI_EXECUTABLE_PATTERN})"
    rf"(?:{_WINDOWS_EXECUTABLE_SUFFIX_PATTERN})(?=$|[\s\"';&|])"
)
_PROVIDER_PYTHON_MODULES = frozenset({
    "anthropic",
    "claude",
    "claude_code",
    "codex",
    "openai",
})
_SHELL_EXECUTABLES = frozenset({
    "bash",
    "dash",
    "fish",
    "ksh",
    "sh",
    "zsh",
})
_COMMAND_WRAPPER_EXECUTABLES = frozenset({
    "busybox",
    "gtimeout",
    "nice",
    "nohup",
    "timeout",
})
_PYTHON_EXECUTABLE_RE = re.compile(r"^(?:py|python|python\d+(?:\.\d+)?)$")
_SHELL_CONTROL_CHARS = frozenset(";&|")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_NUMBER_PATTERN = r"(?P<score>-?\d+(?:\.\d+)?)"


@dataclass(frozen=True)
class SelfEvalCommand:
    name: str
    command: str


@dataclass(frozen=True)
class SelfEvalScope:
    allow: list[str]
    exclude: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SelfEvalMetric:
    name: str
    direction: str
    pattern: str = _NUMBER_PATTERN


@dataclass(frozen=True)
class SelfEvalOutput:
    dir: str
    iterations: str = "iterations.tsv"
    summary: str = "summary.md"


@dataclass(frozen=True)
class SelfEvalContract:
    version: str
    goal: str
    scope: SelfEvalScope
    verify: SelfEvalCommand
    metric: SelfEvalMetric
    guards: list[SelfEvalCommand]
    output: SelfEvalOutput


class SelfEvalContractError(ValueError):
    """Raised when a self-eval YAML contract fails closed."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("invalid self-eval contract: " + "; ".join(self.errors))


def load_self_eval_contract(path: Path) -> SelfEvalContract:
    """Read and validate a self-eval YAML contract.

    This function is intentionally side-effect free: it does not create output
    directories and never executes verify or guard commands.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SelfEvalContractError([f"{path}: contract file not found"]) from exc
    except OSError as exc:
        raise SelfEvalContractError([f"{path}: cannot read contract: {exc}"]) from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SelfEvalContractError([f"{path}: invalid YAML: {exc}"]) from exc
    return parse_self_eval_contract(data, source=str(path))


def parse_self_eval_contract(data: Any, *, source: str = "<contract>") -> SelfEvalContract:
    """Validate raw YAML data and return a typed self-eval contract."""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise SelfEvalContractError([f"{source}: contract must be a mapping"])

    version = _parse_version(data.get("version"))
    goal = _required_text(data, "goal", errors)
    scope = _parse_scope(data.get("scope"), errors)
    verify = _parse_verify(data.get("verify"), errors)
    metric = _parse_metric(data.get("metric"), errors)
    guards = _parse_guards(data.get("guards"), errors)
    output = _parse_output(data.get("output"), errors)

    if errors:
        raise SelfEvalContractError(errors)
    return SelfEvalContract(
        version=version,
        goal=goal,
        scope=scope,
        verify=verify,
        metric=metric,
        guards=guards,
        output=output,
    )


def split_local_command(command: str) -> tuple[dict[str, str], list[str]]:
    """Split a validated local command into env overrides and argv."""
    tokens = _split_command(command)
    env: dict[str, str] = {}
    while tokens and _ENV_ASSIGNMENT_RE.match(tokens[0]):
        key, value = tokens.pop(0).split("=", 1)
        env[key] = value
    if not tokens:
        raise SelfEvalContractError(["command must contain an executable"])
    return env, tokens


def _parse_version(value: object) -> str:
    if value is None:
        return "1"
    return str(value)


def _parse_scope(value: object, errors: list[str]) -> SelfEvalScope:
    if not isinstance(value, dict):
        errors.append("scope must be a mapping with non-empty allow")
        return SelfEvalScope(allow=[])
    allow = _string_list(value.get("allow"), "scope.allow", errors, required=True)
    if not allow:
        errors.append("scope.allow must contain at least one path pattern")
    exclude = _string_list(value.get("exclude", []), "scope.exclude", errors)
    return SelfEvalScope(allow=allow, exclude=exclude)


def _parse_verify(value: object, errors: list[str]) -> SelfEvalCommand:
    if not isinstance(value, dict):
        errors.append("verify must be a mapping with command")
        return SelfEvalCommand(name="verify", command="")
    command = _required_text(value, "command", errors, path="verify")
    if command:
        _validate_local_command(command, "verify.command", errors)
    return SelfEvalCommand(name="verify", command=command)


def _parse_metric(value: object, errors: list[str]) -> SelfEvalMetric:
    if not isinstance(value, dict):
        errors.append("metric must be a mapping with name and direction")
        return SelfEvalMetric(name="", direction="")
    name = _required_text(value, "name", errors, path="metric")
    direction = _required_text(value, "direction", errors, path="metric")
    if direction and direction not in _VALID_METRIC_DIRECTIONS:
        errors.append(
            "metric.direction must be one of "
            f"{sorted(_VALID_METRIC_DIRECTIONS)}, got {direction!r}"
        )
    pattern = str(value.get("pattern") or _NUMBER_PATTERN).strip()
    try:
        re.compile(pattern)
    except re.error as exc:
        errors.append(f"metric.pattern is not a valid regex: {exc}")
    return SelfEvalMetric(name=name, direction=direction, pattern=pattern)


def _parse_guards(value: object, errors: list[str]) -> list[SelfEvalCommand]:
    if value is None:
        errors.append("guards must be present as a list")
        return []
    if not isinstance(value, list):
        errors.append("guards must be a list")
        return []
    guards: list[SelfEvalCommand] = []
    for i, raw_guard in enumerate(value):
        path = f"guards[{i}]"
        if not isinstance(raw_guard, dict):
            errors.append(f"{path} must be a mapping with name and command")
            continue
        name = _required_text(raw_guard, "name", errors, path=path)
        command = _required_text(raw_guard, "command", errors, path=path)
        if command:
            _validate_local_command(command, f"{path}.command", errors)
        guards.append(SelfEvalCommand(name=name, command=command))
    return guards


def _parse_output(value: object, errors: list[str]) -> SelfEvalOutput:
    if not isinstance(value, dict):
        errors.append("output must be a mapping with dir")
        return SelfEvalOutput(dir="")
    out_dir = _required_text(value, "dir", errors, path="output")
    iterations = str(value.get("iterations") or "iterations.tsv").strip()
    summary = str(value.get("summary") or "summary.md").strip()
    for label, item in (("output.iterations", iterations), ("output.summary", summary)):
        if not item or "/" in item or item in {".", ".."}:
            errors.append(f"{label} must be a simple file name")
    return SelfEvalOutput(dir=out_dir, iterations=iterations, summary=summary)


def _required_text(
    mapping: dict[str, object],
    key: str,
    errors: list[str],
    *,
    path: str = "",
) -> str:
    label = f"{path}.{key}" if path else key
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return ""
    return value.strip()


def _string_list(
    value: object,
    path: str,
    errors: list[str],
    *,
    required: bool = False,
) -> list[str]:
    if value is None:
        if required:
            errors.append(f"{path} is required")
        return []
    if not isinstance(value, list):
        errors.append(f"{path} must be a list of non-empty strings")
        return []
    items: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{path}[{i}] must be a non-empty string")
            continue
        items.append(item.strip())
    return items


def _validate_local_command(command: str, path: str, errors: list[str]) -> None:
    control_operator = _first_shell_control_operator(command)
    if control_operator:
        errors.append(
            f"{path} uses shell control operator {control_operator!r}; "
            "self-eval commands must be single local commands"
        )
        return
    provider_executable = _raw_windows_provider_executable(command)
    if provider_executable:
        errors.append(
            f"{path} invokes provider CLI {provider_executable!r}; "
            "self-eval commands must be local deterministic checks"
        )
        return
    tokens = _split_command(command)
    if not tokens:
        errors.append(f"{path} must contain an executable")
        return

    try:
        _validate_command_tokens(tokens, path, errors)
    except SelfEvalContractError as exc:
        errors.extend(f"{path}: {error}" for error in exc.errors)


def _validate_command_tokens(tokens: list[str], path: str, errors: list[str]) -> None:
    while tokens and _ENV_ASSIGNMENT_RE.match(tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        errors.append(f"{path} must contain an executable")
        return
    executable = _executable_name(tokens[0])
    if executable in _PROVIDER_CLI_EXECUTABLES:
        errors.append(
            f"{path} invokes provider CLI {executable!r}; "
            "self-eval commands must be local deterministic checks"
        )
        return
    if executable == "env":
        errors.append(
            f"{path} uses env wrapper; "
            "use leading KEY=value assignments for deterministic local env overrides"
        )
        return
    if executable in _COMMAND_WRAPPER_EXECUTABLES:
        wrapped_tokens = _wrapped_command_tokens(executable, tokens[1:])
        if wrapped_tokens:
            _validate_command_tokens(wrapped_tokens, path, errors)
        return
    if executable in _SHELL_EXECUTABLES and _shell_c_option(tokens[1:]):
        errors.append(
            f"{path} invokes shell -c via {executable!r}; "
            "self-eval commands must not hide provider calls in shell wrappers"
        )
        return
    if _PYTHON_EXECUTABLE_RE.match(executable):
        module = _python_module(tokens[1:])
        if module and _provider_module(module):
            errors.append(
                f"{path} invokes provider Python module {module!r}; "
                "self-eval commands must not call LLM/provider APIs"
            )


def _wrapped_command_tokens(executable: str, args: list[str]) -> list[str]:
    if executable in {"busybox", "nohup"}:
        return args
    if executable in {"timeout", "gtimeout"}:
        return _timeout_command_tokens(args)
    if executable == "nice":
        return _nice_command_tokens(args)
    return []


def _timeout_command_tokens(args: list[str]) -> list[str]:
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "--":
            idx += 1
            break
        if token in {"-k", "--kill-after", "-s", "--signal"}:
            idx += 2
            continue
        if token.startswith(("--kill-after=", "--signal=")):
            idx += 1
            continue
        if token in {"--foreground", "--preserve-status", "-v"}:
            idx += 1
            continue
        if token.startswith("-"):
            idx += 1
            continue
        break
    if idx >= len(args):
        return []
    return args[idx + 1 :]


def _nice_command_tokens(args: list[str]) -> list[str]:
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "--":
            return args[idx + 1 :]
        if token in {"-n", "--adjustment"}:
            idx += 2
            continue
        if token.startswith("--adjustment="):
            idx += 1
            continue
        if re.match(r"^-(?:n)?\d+$", token):
            idx += 1
            continue
        if token.startswith("-"):
            idx += 1
            continue
        return args[idx:]
    return []


def _shell_c_option(tokens: list[str]) -> bool:
    for token in tokens:
        if token == "--":
            return False
        if token == "-c":
            return True
        if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
            return True
    return False


def _python_module(tokens: list[str]) -> str:
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token == "--":
            return ""
        module = _python_module_from_option(token, tokens[idx + 1 :])
        if module is not None:
            return module
        idx += 1
    return ""


def _python_module_from_option(token: str, following: list[str]) -> str | None:
    if not token.startswith("-") or token == "-" or token.startswith("--"):
        return None
    chars = token[1:]
    idx = 0
    while idx < len(chars):
        option = chars[idx]
        if option == "c":
            return ""
        if option == "m":
            attached = chars[idx + 1 :]
            return attached or (following[0] if following else "")
        if option in {"W", "X"}:
            return None
        idx += 1
    return None


def _provider_module(module: str) -> bool:
    root = module.split(".", 1)[0].replace("-", "_").lower()
    return root in _PROVIDER_PYTHON_MODULES


def _executable_name(raw: str) -> str:
    name = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    for suffix in _WINDOWS_EXECUTABLE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _raw_windows_provider_executable(command: str) -> str:
    match = _WINDOWS_PROVIDER_EXECUTABLE_RE.search(command)
    if match is None:
        return ""
    return match.group("name").lower()


def _first_shell_control_operator(command: str) -> str:
    quote = ""
    escaped = False
    idx = 0
    while idx < len(command):
        char = command[idx]
        if escaped:
            escaped = False
            idx += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            idx += 1
            continue
        if quote:
            if char == quote:
                quote = ""
            idx += 1
            continue
        if char in {"'", '"'}:
            quote = char
            idx += 1
            continue
        if char in _SHELL_CONTROL_CHARS:
            end = idx + 1
            while end < len(command) and command[end] in _SHELL_CONTROL_CHARS:
                end += 1
            return command[idx:end]
        idx += 1
    return ""


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError as exc:
        raise SelfEvalContractError([f"invalid command syntax: {exc}"]) from exc
