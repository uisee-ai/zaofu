"""Task-map validation and projection helpers.

``task-map.json`` bridges human planning artifacts and executable kanban
contracts. The orchestrator owns semantic decomposition; helpers here only
perform deterministic schema/topology checks and lightweight summaries.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskMapValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_check(self) -> dict[str, Any]:
        return {
            "name": "task_map_validate",
            "passed": self.passed,
            "errors": list(self.errors),
            "summary": dict(self.summary),
        }


def load_task_map(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("task-map must be a JSON object")
    return data


def load_source_index(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("source-index must be a JSON object")
    return data


def build_task_map_from_ingest_plan(
    plan: dict[str, Any],
    *,
    source_refs: dict[str, str],
) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    for item in plan.get("tasks", []):
        if not isinstance(item, dict):
            continue
        tasks.append({
            "task_id": str(item.get("id") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "owner_role": str(item.get("owner_role") or "").strip(),
            "phase": str(item.get("phase") or item.get("plan_section") or "").strip(),  # doc 69 S-b
            "plan_section": str(item.get("plan_section") or item.get("key") or "").strip(),
            "blocked_by": _string_list(item.get("blocked_by")),
            "wave": _int_value(item.get("wave")),
            "scope": _string_list(item.get("scope")),
            "shared_files": _string_list(item.get("shared_files")),
            "exclusive_files": _string_list(item.get("exclusive_files")),
            "acceptance": _string_list(item.get("acceptance")),
            "verification": normalize_verification_command(item.get("verification")),
            "verification_tiers": _string_list(item.get("verification_tiers")),
        })
    return {
        "schema_version": "task-map.v1",
        "feature_id": str(plan.get("feature_id") or "").strip(),
        "source_refs": dict(source_refs),
        "tasks": tasks,
    }


def validate_task_map_payload(
    payload: dict[str, Any],
    *,
    require_task_verification: bool = True,
) -> TaskMapValidationResult:
    errors: list[str] = []
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version and schema_version != "task-map.v1":
        errors.append(f"unsupported schema_version {schema_version!r}")
    tasks_raw = payload.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        errors.append("tasks must be a non-empty list")
        tasks_raw = []

    ids: list[str] = []
    exclusive_claims: dict[str, str] = {}
    waves: dict[str, int] = {}
    task_count_by_wave: dict[str, int] = {}
    missing_allowed_path_reason: list[str] = []
    assembly_owner_roles: dict[str, str] = {}
    bundle_owner_roles: dict[str, str] = {}
    task_dependencies: dict[str, list[str]] = {}
    quality_contract = (
        payload.get("quality_contract")
        if isinstance(payload.get("quality_contract"), dict)
        else {}
    )
    require_inventory_binding = _truthy(
        payload.get("require_inventory_binding")
        or quality_contract.get("require_inventory_binding")
    )
    require_non_smoke = _truthy(
        quality_contract.get("require_non_smoke_for_blocking_inventory"),
        default=True,
    )
    blocking_priorities = {
        item.strip().upper()
        for item in (
            _string_list(quality_contract.get("blocking_priorities"))
            or ["P0", "P1"]
        )
        if item.strip()
    }
    # A task's verification COMMAND runs tests (read-only) and may legitimately
    # reference files owned by sibling tasks in the same plan — e.g. the refactor
    # flow's characterization-test pattern, where the refactor task verifies
    # against a test file written by a separate characterization task. Scope the
    # verification check against the whole plan's paths so cross-task test runs
    # are allowed while truly out-of-plan references are still caught.
    plan_scope_paths: list[str] = []
    wave_by_task = _wave_by_task_id(payload)
    for raw in tasks_raw:
        if isinstance(raw, dict):
            plan_scope_paths.extend(_string_list(raw.get("allowed_paths")))
            plan_scope_paths.extend(_string_list(raw.get("exclusive_files")))
    for idx, raw in enumerate(tasks_raw):
        if not isinstance(raw, dict):
            errors.append(f"tasks[{idx}] must be an object")
            continue
        task_id = _task_id(raw)
        if not task_id:
            errors.append(f"tasks[{idx}].task_id is required")
            continue
        if task_id in ids:
            errors.append(f"duplicate task_id {task_id!r}")
        ids.append(task_id)
        wave = _int_value(raw.get("wave") if raw.get("wave") is not None else wave_by_task.get(task_id))
        waves[task_id] = wave
        task_count_by_wave[str(wave)] = task_count_by_wave.get(str(wave), 0) + 1
        owner_role = str(raw.get("owner_role") or "").strip()
        task_dependencies[task_id] = _string_list(raw.get("blocked_by"))
        if str(raw.get("root_owner_class") or "").strip().lower() == "assembly":
            if owner_role:
                assembly_owner_roles[task_id] = owner_role
        elif owner_role:
            bundle_owner_roles[task_id] = owner_role
        if (
            require_task_verification
            and not normalize_verification_command(raw.get("verification"))
            and not _string_list(raw.get("acceptance"))
            and not _validation_command(raw)
        ):
            errors.append(f"{task_id} requires verification or acceptance")
        for field, command in _verification_command_fields(raw):
            for error in _verification_command_errors(command):
                errors.append(f"{task_id}.{field} {error}")
            for error in _verification_scope_errors(
                raw,
                command,
                plan_scope_paths,
                relative_roots=_relative_path_roots(payload),
            ):
                errors.append(f"{task_id}.{field} {error}")
            for error in _verification_level_errors(command):
                errors.append(f"{task_id}.{field} {error}")
        if _string_list(raw.get("allowed_paths")) and not str(
            raw.get("allowed_paths_reason") or raw.get("scope_reason") or ""
        ).strip():
            missing_allowed_path_reason.append(task_id)
        if require_inventory_binding:
            errors.extend(
                _inventory_binding_errors(
                    raw,
                    blocking_priorities=blocking_priorities,
                    require_non_smoke=require_non_smoke,
                )
            )
        for path in _string_list(raw.get("exclusive_files")):
            owner = exclusive_claims.get(path)
            if owner and owner != task_id:
                errors.append(f"exclusive_files overlap {path!r}: {owner} and {task_id}")
            exclusive_claims[path] = task_id

    ids_set = set(ids)
    for idx, raw in enumerate(tasks_raw):
        if not isinstance(raw, dict):
            continue
        task_id = _task_id(raw) or f"tasks[{idx}]"
        for dep in _string_list(raw.get("blocked_by")):
            if dep not in ids_set:
                errors.append(f"{task_id}.blocked_by references unknown task {dep!r}")
                continue
            if waves.get(dep, 0) > waves.get(task_id, 0):
                errors.append(f"{task_id}.blocked_by {dep!r} is in a later wave")

    source_refs = payload.get("source_refs")
    if source_refs is not None and not isinstance(source_refs, dict):
        errors.append("source_refs must be an object when present")
    for field, value in _workspace_root_owner_requirement_values(payload):
        if not isinstance(value, bool):
            errors.append(f"{field} must be a boolean when present")

    errors.extend(_shared_convention_errors(
        payload,
        [raw for raw in tasks_raw if isinstance(raw, dict)],
    ))
    errors.extend(_assembly_ownership_errors(
        assembly_owner_roles=assembly_owner_roles, bundle_owner_roles=bundle_owner_roles,
        task_dependencies=task_dependencies,
    ))

    summary = {
        "task_count": len(ids),
        "wave_count": len(task_count_by_wave),
        "tasks_by_wave": task_count_by_wave,
        "exclusive_file_count": len(exclusive_claims),
        "tasks_missing_allowed_paths_reason": missing_allowed_path_reason,
        "bundle_owner_count": len(set(bundle_owner_roles.values())),
        "assembly_task_count": len(assembly_owner_roles),
    }
    return TaskMapValidationResult(passed=not errors, errors=errors, summary=summary)


def _workspace_root_owner_requirement_values(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return every explicit root-owner requirement for schema validation.

    The planner owns whether a delivery needs a root-level scaffold/entrypoint;
    the kernel owns the representation. Validate both supported locations so a
    string from a prompt or legacy artifact cannot silently disable admission.
    """
    values: list[tuple[str, Any]] = []
    if "workspace_root_owner_required" in payload:
        values.append(("workspace_root_owner_required", payload["workspace_root_owner_required"]))
    contract = payload.get("refactor_contract")
    if isinstance(contract, dict) and "workspace_root_owner_required" in contract:
        values.append((
            "refactor_contract.workspace_root_owner_required",
            contract["workspace_root_owner_required"],
        ))
    return values


def _assembly_ownership_errors(
    *,
    assembly_owner_roles: dict[str, str],
    bundle_owner_roles: dict[str, str],
    task_dependencies: dict[str, list[str]],
) -> list[str]:
    """Reject plans without independent assembly ownership or with a
    dependency owner collision."""
    bundle_owners = set(bundle_owner_roles.values())
    if len(bundle_owners) <= 1:
        return []
    if not assembly_owner_roles:
        return ["缺 assembly 任务: 多个并行 bundle 需要一个独立 root_owner_class=assembly 任务"]
    errors: list[str] = []
    for task_id, owner_role in assembly_owner_roles.items():
        reachable: set[str] = set()
        pending = list(task_dependencies.get(task_id, []))
        while pending:
            dependency_id = pending.pop()
            if dependency_id in reachable:
                continue
            reachable.add(dependency_id)
            pending.extend(task_dependencies.get(dependency_id, []))
        dependency_owner_roles = {
            bundle_owner_roles[dependency_id] for dependency_id in reachable
            if dependency_id in bundle_owner_roles
        }
        if owner_role in dependency_owner_roles:
            errors.append(
                f"{task_id}.owner_role {owner_role!r} 与并行 bundle owner 冲突: "
                "assembly 任务不能和它依赖的 bundle 共用同一 owner_role(自锁)"
            )
    return errors


def _inventory_binding_errors(
    raw: dict[str, Any],
    *,
    blocking_priorities: set[str],
    require_non_smoke: bool,
) -> list[str]:
    task_id = _task_id(raw) or "<unknown>"
    priority = str(raw.get("priority") or "P0").strip().upper()
    if blocking_priorities and priority not in blocking_priorities:
        return []
    errors: list[str] = []
    root_owner_class = str(raw.get("root_owner_class") or "").strip().lower()
    is_assembly = root_owner_class == "assembly"
    inventory_ids = _string_list(raw.get("inventory_ids")) or _string_list(
        raw.get("source_inventory_ids")
    ) or _string_list(raw.get("inventory_refs"))
    if not inventory_ids and not is_assembly:
        errors.append(
            f"{task_id}.inventory_ids or source_inventory_ids is required by quality_contract"
        )
    source_refs = (
        _string_list(raw.get("source_refs"))
        or _string_list(raw.get("source_ref"))
        or _string_list(raw.get("source_keys"))
        or _string_list(raw.get("source_key"))
    )
    if not source_refs:
        errors.append(f"{task_id}.source_refs is required by quality_contract")
    verification = (
        normalize_verification_command(raw.get("verification"))
        or normalize_verification_command(raw.get("verify_commands"))
        or normalize_verification_command(raw.get("verification_commands"))
        or _validation_command(raw)
    )
    if not verification:
        errors.append(f"{task_id}.verification or verify_commands is required by quality_contract")
    if require_non_smoke and not is_assembly and not _truthy(raw.get("non_smoke_test_required")):
        errors.append(f"{task_id}.non_smoke_test_required=true is required by quality_contract")
    return errors


def validate_source_index_payload(
    payload: dict[str, Any],
    *,
    task_map: dict[str, Any] | None = None,
    require_canonical: bool = True,
) -> TaskMapValidationResult:
    """Validate task_id -> source provenance coverage for a task-map.

    The validator is intentionally structural. Layer 2 owns semantic synthesis;
    Layer 1 only proves every executable task has a durable source pointer and
    a captured excerpt or an explicit degraded reason.
    """
    errors: list[str] = []
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version and schema_version != "source-index.v1":
        errors.append(f"unsupported schema_version {schema_version!r}")

    tasks_raw = payload.get("tasks")
    if isinstance(tasks_raw, dict):
        task_entries = [
            {"task_id": key, **value}
            if isinstance(value, dict)
            else {"task_id": key, "source_excerpt": str(value)}
            for key, value in tasks_raw.items()
        ]
    elif isinstance(tasks_raw, list):
        task_entries = tasks_raw
    else:
        errors.append("tasks must be a non-empty list or object")
        task_entries = []
    if not task_entries:
        errors.append("tasks must be a non-empty list or object")

    seen: set[str] = set()
    modes: dict[str, int] = {}
    for idx, raw in enumerate(task_entries):
        if not isinstance(raw, dict):
            errors.append(f"tasks[{idx}] must be an object")
            continue
        task_id = str(raw.get("task_id") or raw.get("id") or "").strip()
        if not task_id:
            errors.append(f"tasks[{idx}].task_id is required")
            continue
        if task_id in seen:
            errors.append(f"duplicate task_id {task_id!r}")
        seen.add(task_id)

        mode = str(raw.get("source_mode") or raw.get("mode") or "canonical").strip()
        modes[mode or "canonical"] = modes.get(mode or "canonical", 0) + 1
        source_key = str(raw.get("source_key") or "").strip()
        source_ref = str(raw.get("source_ref") or raw.get("ref") or "").strip()
        source_excerpt = str(
            raw.get("source_excerpt")
            or raw.get("excerpt")
            or raw.get("text")
            or ""
        ).strip()
        degraded_reason = str(raw.get("degraded_reason") or "").strip()
        if not source_key:
            errors.append(f"{task_id}.source_key is required")
        if not source_ref:
            errors.append(f"{task_id}.source_ref is required")
        if not source_excerpt:
            errors.append(f"{task_id}.source_excerpt is required")
        if require_canonical and mode == "degraded":
            errors.append(f"{task_id}.source_mode=degraded is not accepted on the main path")
        if mode == "degraded" and not degraded_reason:
            errors.append(f"{task_id}.degraded_reason is required")

    missing: list[str] = []
    if task_map is not None:
        validation = validate_task_map_payload(
            task_map,
            require_task_verification=False,
        )
        if not validation.passed:
            errors.extend(f"task_map: {item}" for item in validation.errors)
        for raw in task_map.get("tasks") or []:
            if not isinstance(raw, dict):
                continue
            task_id = _task_id(raw)
            if task_id and task_id not in seen:
                missing.append(task_id)
        for task_id in missing:
            errors.append(f"source_index missing task_id {task_id!r}")

    summary = {
        "source_task_count": len(seen),
        "source_modes": modes,
        "missing_task_count": len(missing),
        "missing_task_ids": missing,
    }
    return TaskMapValidationResult(passed=not errors, errors=errors, summary=summary)


def validate_coverage_report_payload(
    payload: dict[str, Any],
    *,
    task_map: dict[str, Any] | None = None,
) -> TaskMapValidationResult:
    errors: list[str] = []
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version and schema_version != "coverage-report.v1":
        errors.append(f"unsupported schema_version {schema_version!r}")
    tasks = payload.get("tasks")
    coverage_task_ids: list[str] = []
    if not isinstance(tasks, (list, dict)) or not tasks:
        errors.append("tasks must be a non-empty list or object")
    else:
        coverage_task_ids = _coverage_task_ids(tasks)
        if not coverage_task_ids:
            errors.append("tasks must identify at least one task_id")
    unresolved = payload.get("unresolved_unknowns", [])
    if unresolved not in (None, "") and not isinstance(unresolved, list):
        errors.append("unresolved_unknowns must be a list when present")
    expected_task_ids: list[str] = []
    if task_map is not None:
        task_items = task_map.get("tasks") if isinstance(task_map, dict) else None
        if isinstance(task_items, list):
            for raw in task_items:
                if isinstance(raw, dict):
                    task_id = _task_id(raw)
                    if task_id:
                        expected_task_ids.append(task_id)
        missing = [
            task_id for task_id in expected_task_ids
            if task_id not in set(coverage_task_ids)
        ]
        if missing:
            errors.append(
                "coverage_report missing task_id "
                + ", ".join(repr(task_id) for task_id in missing)
            )
    summary = {
        "coverage_task_count": len(coverage_task_ids),
        "expected_task_count": len(expected_task_ids),
        "missing_task_ids": [
            task_id for task_id in expected_task_ids
            if task_id not in set(coverage_task_ids)
        ],
        "unresolved_unknown_count": len(unresolved) if isinstance(unresolved, list) else 0,
    }
    return TaskMapValidationResult(passed=not errors, errors=errors, summary=summary)


def _coverage_task_ids(tasks: list[Any] | dict[str, Any]) -> list[str]:
    ids: list[str] = []
    if isinstance(tasks, dict):
        iterable = tasks.items()
        for key, value in iterable:
            task_id = str(key or "").strip()
            if not task_id and isinstance(value, dict):
                task_id = _task_id(value)
            if task_id and task_id not in ids:
                ids.append(task_id)
        return ids
    for raw in tasks:
        task_id = ""
        if isinstance(raw, dict):
            task_id = _task_id(raw)
        elif isinstance(raw, str):
            task_id = raw.strip()
        if task_id and task_id not in ids:
            ids.append(task_id)
    return ids


def _wave_by_task_id(payload: dict[str, Any]) -> dict[str, int]:
    waves = payload.get("waves")
    if not isinstance(waves, list):
        return {}
    out: dict[str, int] = {}
    for raw in waves:
        if not isinstance(raw, dict):
            continue
        wave = _int_value(raw.get("wave"))
        for task_id in _string_list(raw.get("tasks")):
            out.setdefault(task_id, wave)
    return out


def summarize_task_map_file(path: Path) -> dict[str, Any]:
    payload = load_task_map(path)
    result = validate_task_map_payload(payload)
    return {
        "path": str(path),
        "passed": result.passed,
        "errors": list(result.errors),
        **result.summary,
    }


def resolve_artifact_file(raw_path: str, *, project_root: Path, state_dir: Path) -> Path:
    from zf.runtime.artifact_refs import resolve_runtime_artifact_ref

    return resolve_runtime_artifact_ref(
        raw_path,
        project_root=project_root,
        state_dir=state_dir,
    )


def _task_id(raw: dict[str, Any]) -> str:
    return str(raw.get("task_id") or raw.get("id") or "").strip()


def _validation_command(raw: dict[str, Any]) -> str:
    validation = raw.get("validation")
    if not isinstance(validation, dict):
        return ""
    return str(validation.get("command") or "").strip()


def normalize_verification_command(value: Any) -> str:
    if isinstance(value, list):
        return " && ".join(
            str(item).strip() for item in value if str(item or "").strip()
        )
    return str(value or "").strip()


def _verification_command_fields(raw: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    verification = normalize_verification_command(raw.get("verification"))
    if verification:
        fields.append(("verification", verification))
    validation_command = _validation_command(raw)
    if validation_command:
        fields.append(("validation.command", validation_command))
    return fields


def _verification_command_errors(command: str) -> list[str]:
    text = str(command or "").strip()
    if not text:
        return []
    if _verification_contains_prose(text):
        return [
            "must be an executable command only; put expected-red/prose in "
            "validation or evidence_contract"
        ]
    early_errors = [
        *_single_quoted_bash_c_errors(text),
        *_unquoted_glob_filter_errors(text),
        *_unquoted_path_glob_errors(text),
    ]
    if early_errors:
        return early_errors
    try:
        parsed = subprocess.run(
            ["sh", "-n", "-c", text],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"could not be parsed by sh -n: {exc}"]
    if parsed.returncode != 0:
        detail = (parsed.stderr or parsed.stdout or "").strip()
        if detail:
            return [f"must be valid shell syntax: {detail}"]
        return ["must be valid shell syntax"]
    return []


_UNQUOTED_FILTER_GLOB_RE = re.compile(
    r"(?<!\S)--filter(?:\s+|=)(\./[^\s'\";|&]*[*?\[][^\s'\";|&]*)"
)
_BASH_C_SINGLE_QUOTED_RE = re.compile(
    r"\bbash\s+-[A-Za-z]*c[A-Za-z]*\s+'"
)


def _single_quoted_bash_c_errors(command: str) -> list[str]:
    """Reject lossy `bash -lc '...'` wrappers with raw inner single quotes.

    `sh -n` cannot catch this class: the outer shell treats the inner quote as
    closing/reopening the command argument, so syntax still parses while code
    literals such as `require('node:fs')` are stripped before the inner bash
    sees them. R37 hit this as an assembly verification false runtime failure.
    """
    if not _BASH_C_SINGLE_QUOTED_RE.search(command):
        return []
    try:
        argv = shlex.split(command)
    except ValueError:
        return []
    if len(argv) != 3:
        return []
    if Path(argv[0]).name != "bash":
        return []
    option = argv[1]
    if not option.startswith("-") or "c" not in option:
        return []
    match = _BASH_C_SINGLE_QUOTED_RE.search(command)
    if match is None:
        return []
    tail = command[match.end():]
    if tail.count("'") <= 1:
        return []
    if "'\\''" in tail or "'\"'\"'" in tail:
        return []
    return [
        "must not wrap bash -c payload in single quotes when the payload "
        "contains inner single quotes; use a script file or a double-quoted "
        "wrapper with proper escaping"
    ]


def _unquoted_glob_filter_errors(command: str) -> list[str]:
    matches = [
        match.group(1)
        for match in _UNQUOTED_FILTER_GLOB_RE.finditer(command)
    ]
    if not matches:
        return []
    unique = sorted(set(matches))
    return [
        "must quote shell glob filter arguments before the shell expands them: "
        + ", ".join(unique)
    ]


def _unquoted_path_glob_errors(command: str) -> list[str]:
    tokens = _shell_tokens_with_quote_state(command)
    matches = sorted({
        token
        for token, quoted in tokens
        if not quoted
        and any(ch in token for ch in "*?[")
        and "/" in token
        and "--filter" not in token
    })
    if not matches:
        return []
    return [
        "must quote shell glob path arguments before the shell expands them: "
        + ", ".join(matches)
    ]


def _shell_tokens_with_quote_state(command: str) -> list[tuple[str, bool]]:
    tokens: list[tuple[str, bool]] = []
    current: list[str] = []
    quote = ""
    quoted = False
    escaped = False
    for ch in command:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            continue
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in {"'", '"'}:
            quoted = True
            current.append(ch)
            quote = ch
            continue
        if ch.isspace() or ch in {";", "|", "&"}:
            if current:
                token = "".join(current).strip()
                if token:
                    tokens.append((token.strip("'\""), quoted))
                current = []
                quoted = False
            continue
        current.append(ch)
    if current:
        token = "".join(current).strip()
        if token:
            tokens.append((token.strip("'\""), quoted))
    return tokens


# C2(prd-goal e2e finding-2/14,验证层级错配):切片任务的 verification
# 在隔离 task 分支上执行,系统级命令(安装整包/入口冒烟)只有集成后的
# candidate 才可能通过——挂错层 = 结构性必败 + 返工路由永远错位
# (3 轮同指纹实弹)。W1 原来只查文件引用,此处补命令类别维度。
_SYSTEM_LEVEL_COMMAND_PATTERNS = (
    re.compile(r"pip3?\s+install\s+(-e\b|\.($|\s)|--editable\b)"),
    re.compile(r"python3?\s+-m\s+pip\s+install\s+(-e\b|\.($|\s)|--editable\b)"),
)


def _shared_convention_errors(
    data: dict[str, Any],
    task_items: list[dict[str, Any]],
) -> list[str]:
    """C1(prd-goal e2e finding-5/7):跨任务共享约定单源。

    v1 规则:task_map.shared_conventions.test_path_prefix 存在时,所有
    任务 allowed_paths/verification 引用的测试文件路径必须以该前缀开头
    ——textstat 实弹里 tests/ 与 app/tests/ 四家各表,verify 逐层考古
    式返工的直接根源。"""
    conventions = data.get("shared_conventions")
    if not isinstance(conventions, dict):
        return []
    prefix = str(conventions.get("test_path_prefix") or "").strip()
    if not prefix:
        return []
    prefix = prefix.rstrip("/") + "/"
    prefix_base = prefix.rstrip("/")
    relative_roots = _relative_path_roots(data)
    out: list[str] = []
    test_like = re.compile(r"(^|/)(tests?)(/|$)|test_[^/]*\.py$")
    for raw in task_items:
        task_id = str(raw.get("task_id") or "?")
        refs = list(_string_list(raw.get("allowed_paths")))
        for _field, command in _verification_command_fields(raw):
            refs.extend(_command_path_refs(command))
        for ref in refs:
            norm = str(ref).lstrip("./")
            if not test_like.search(norm):
                continue
            if norm == prefix_base or norm.startswith(prefix):
                continue
            rooted = [
                _normalize_scope_path(f"{root}/{norm}")
                for root in relative_roots
                if _can_apply_relative_root(norm)
            ]
            if not any(item == prefix_base or item.startswith(prefix) for item in rooted):
                out.append(
                    f"{task_id} test path {ref!r} violates shared convention "
                    f"test_path_prefix={prefix!r}"
                )
    return out


def _verification_level_errors(command: str) -> list[str]:
    text = str(command or "")
    for pattern in _SYSTEM_LEVEL_COMMAND_PATTERNS:
        if pattern.search(text):
            return [
                "uses a system-level command (package install) that cannot "
                "succeed on an isolated task slice; move it to the "
                "integration/test stage checks: "
                f"{text[:120]!r}"
            ]
    return []


def _verification_scope_errors(
    raw: dict[str, Any],
    command: str,
    plan_scope_paths: list[str] | None = None,
    *,
    relative_roots: list[str] | None = None,
) -> list[str]:
    allowed_paths = _string_list(raw.get("allowed_paths"))
    if not allowed_paths:
        return []
    scope_paths = [
        *_string_list(raw.get("allowed_paths")),
        *_string_list(raw.get("exclusive_files")),
        # verification commands run tests read-only; allow any test/file owned by
        # a sibling task in the same plan (cross-task verification).
        *(plan_scope_paths or []),
    ]
    if not scope_paths:
        return []
    out: list[str] = []
    for path_ref in _command_path_refs(command):
        if not _path_ref_covered(
            path_ref,
            scope_paths,
            relative_roots=relative_roots,
        ):
            out.append(
                "references path outside allowed_paths/exclusive_files: "
                f"{path_ref!r}"
            )
    return out


_CODE_ARGUMENT_FLAGS = {
    "-c",
    "-e",
    "-ec",
    "-lc",
    "-lec",
    "--command",
    "--eval",
}


def _command_path_refs(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    refs: list[str] = []
    skip_code_argument = False
    for token in tokens:
        if skip_code_argument:
            skip_code_argument = False
            continue
        if str(token).strip().lower() in _CODE_ARGUMENT_FLAGS:
            skip_code_argument = True
            continue
        for cleaned in _path_ref_candidates_from_shell_token(token):
            # A pytest node-id (path::test_name, optionally path::Class::test) is a
            # test target, not a separate file. Validate only the file part so an
            # in-scope test file is not rejected for naming a specific node — e.g.
            # `pytest tests/test_x.py::test_case` must match allowed path
            # `tests/test_x.py`, not the literal `tests/test_x.py::test_case`.
            if "::" in cleaned:
                cleaned = cleaned.split("::", 1)[0]
            if not _looks_like_path_ref(cleaned):
                continue
            refs.append(cleaned)
    return list(dict.fromkeys(refs))


def _path_ref_candidates_from_shell_token(token: str) -> list[str]:
    cleaned = _clean_path_ref_token(token)
    if not cleaned or cleaned.startswith("-") or "://" in cleaned:
        return []
    if cleaned.startswith("$("):
        inner = cleaned[2:].strip()
        if inner.endswith(")"):
            inner = inner[:-1].strip()
        try:
            inner_tokens = shlex.split(inner)
        except ValueError:
            inner_tokens = inner.split()
        out: list[str] = []
        for inner_token in inner_tokens:
            out.extend(_path_ref_candidates_from_shell_token(inner_token))
        return out
    if any(ch.isspace() for ch in cleaned):
        return []
    if "=" in cleaned and not cleaned.startswith(("./", "../", "/")):
        cleaned = cleaned.rsplit("=", 1)[-1]
    cleaned = _clean_path_ref_token(cleaned)
    return [cleaned] if cleaned else []


def _clean_path_ref_token(token: str) -> str:
    cleaned = str(token or "").strip().strip("'\"")
    # Path refs often appear in generated acceptance/prose as
    # ``tests/foo.py.`` or ``src/app.ts)``. Treat sentence punctuation as
    # prose, not as part of the path, while leaving real internal path
    # characters untouched.
    return cleaned.rstrip(".,;:)]}，。；：、）】》")


def _looks_like_path_ref(token: str) -> bool:
    if token in {".", ".."}:
        return False
    if token.startswith(("./", "../", "/")):
        return True
    return "/" in token and not token.startswith("-")


def _path_ref_covered(
    path_ref: str,
    scope_paths: list[str],
    *,
    relative_roots: list[str] | None = None,
) -> bool:
    ref = _normalize_scope_path(path_ref)
    if not ref:
        return True
    refs = [ref]
    if _can_apply_relative_root(path_ref):
        for root in relative_roots or []:
            rooted = _normalize_scope_path(f"{root}/{path_ref}")
            if rooted and rooted not in refs:
                refs.append(rooted)
    for candidate_ref in refs:
        for scope in scope_paths:
            normalized_scope = _normalize_scope_path(scope)
            if not normalized_scope:
                return True
            if candidate_ref == normalized_scope:
                return True
            if candidate_ref.startswith(f"{normalized_scope}/"):
                return True
            if normalized_scope.startswith(f"{candidate_ref}/"):
                return True
    return False


def _can_apply_relative_root(path_ref: str) -> bool:
    text = str(path_ref or "").strip()
    return bool(text) and not text.startswith(("./", "../", "/"))


def _relative_path_roots(payload: dict[str, Any]) -> list[str]:
    roots: list[str] = []
    conventions = payload.get("shared_conventions")
    if isinstance(conventions, dict):
        for key in ("package_root", "target_root"):
            value = str(conventions.get(key) or "").strip()
            if value:
                roots.append(_normalize_scope_path(value))
        run_cwd = str(conventions.get("run_cwd") or "").strip()
        if run_cwd:
            roots.append(_normalize_scope_path(run_cwd.split()[0]))
    target_root = str(payload.get("target_root") or "").strip()
    if target_root:
        roots.append(_normalize_scope_path(target_root))
    return [root for root in dict.fromkeys(roots) if root]


def _normalize_scope_path(path: str) -> str:
    text = str(path or "").strip().strip("'\"")
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    parts: list[str] = []
    for part in text.split("/"):
        if not part or part == ".":
            continue
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    return "/".join(parts)


def _verification_contains_prose(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return True
    if any(ch in text for ch in ("（", "）", "；")):
        return True
    lowered = text.lower()
    prose_markers = (
        "expected red",
        "red expected",
        "expected failure",
        "should fail",
        "evidence:",
    )
    return any(marker in lowered for marker in prose_markers)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
