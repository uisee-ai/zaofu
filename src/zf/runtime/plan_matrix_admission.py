"""Mechanical cross-port admission for one canonical Plan artifact package."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any


_MATRIX_ROWS = {
    "capability_matrix": ("capabilities",),
    "acceptance_matrix": ("acceptance", "acceptances"),
    "test_matrix": ("commands",),
    "real_e2e_matrix": ("rows",),
}


def plan_matrix_admission_errors(
    *,
    task_map: Mapping[str, Any],
    source_index: Mapping[str, Any],
    ports: Mapping[str, Mapping[str, Any]],
    required_ports: list[str],
) -> list[dict[str, str]]:
    """Prove task, acceptance, command, capability, test, and source closure."""

    errors: list[dict[str, str]] = []
    required = set(required_ports)
    for name in _MATRIX_ROWS:
        body = ports.get(name)
        if body is None:
            continue
        if name in required:
            _matrix_readiness_errors(name, body, errors)
    # Match package admission for enriched Task Maps and source inventories;
    # legacy bodies without an enrichment contract remain valid.
    for name, body in (
        ("task_map", task_map),
        ("source_inventory", ports.get("source_inventory")),
    ):
        if name not in required or not isinstance(body, Mapping):
            continue
        metadata = body.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(
            metadata.get("enrichment_contract"), Mapping
        ):
            _matrix_readiness_errors(name, body, errors)

    tasks = _rows(task_map, "tasks")
    task_by_id = _unique_rows(tasks, "task", ("task_id", "id"), errors)
    task_acceptance: dict[str, tuple[str, Mapping[str, Any]]] = {}
    task_commands: dict[str, tuple[str, Mapping[str, Any], str]] = {}
    for task_id, task in task_by_id.items():
        for index, criterion in enumerate(_rows(task, "acceptance_criteria")):
            acceptance_id = _row_id(criterion, "id", "acceptance_id")
            field = f"task_map.tasks[{task_id}].acceptance_criteria[{index}]"
            if not acceptance_id:
                _error(errors, "acceptance_id_missing", field, "acceptance row requires id")
                continue
            if acceptance_id in task_acceptance:
                _error(
                    errors,
                    "acceptance_id_duplicate",
                    field,
                    f"acceptance id {acceptance_id!r} has multiple task producers",
                )
                continue
            task_acceptance[acceptance_id] = (task_id, criterion)
        for index, command in enumerate(_task_command_rows(task)):
            command_id = _row_id(command, "id", "command_id")
            field = f"task_map.tasks[{task_id}].validation.commands[{index}]"
            if not command_id:
                _error(errors, "command_id_missing", field, "command requires a stable id")
                continue
            if command_id in task_commands:
                _error(
                    errors,
                    "command_id_duplicate",
                    field,
                    f"command id {command_id!r} has multiple task producers",
                )
                continue
            command_body = str(command.get("command") or command.get("cmd") or "").strip()
            if not command_body:
                _error(errors, "command_definition_missing", field, "command body is required")
            root = _command_root(command, task, task_map)
            if not _safe_execution_root(root):
                _error(
                    errors,
                    "command_execution_root_invalid",
                    f"{field}.execution_root",
                    f"command execution root must be a safe relative path, got {root!r}",
                )
            task_commands[command_id] = (task_id, command, root)

    acceptance_body = ports.get("acceptance_matrix")
    acceptance_rows = _rows(acceptance_body or {}, "acceptance", "acceptances")
    acceptance_by_id = _unique_rows(
        acceptance_rows,
        "acceptance_matrix.acceptance",
        ("acceptance_id", "id"),
        errors,
    )
    if acceptance_body is not None:
        _set_mismatch_errors(
            errors,
            left=set(task_acceptance),
            right=set(acceptance_by_id),
            left_name="task_map.acceptance",
            right_name="acceptance_matrix.acceptance",
            code="acceptance_set_mismatch",
        )

    for acceptance_id, row in acceptance_by_id.items():
        expected_task = task_acceptance.get(acceptance_id, ("", {}))[0]
        task_refs = _refs(row, "task_id", "task_ids", "owning_task_id")
        _known_refs(errors, task_refs, set(task_by_id), f"acceptance_matrix.{acceptance_id}.task_ids", "task")
        if expected_task and task_refs and task_refs != {expected_task}:
            _error(
                errors,
                "acceptance_task_mismatch",
                f"acceptance_matrix.{acceptance_id}.task_ids",
                f"acceptance belongs to task {expected_task!r}, got {sorted(task_refs)}",
            )
        if expected_task and not task_refs:
            _error(
                errors,
                "acceptance_task_missing",
                f"acceptance_matrix.{acceptance_id}.task_ids",
                "acceptance row must identify its task",
            )
        command_refs = _refs(row, "verification_command_ids", "command_ids")
        task_refs_expected = _refs(
            task_acceptance.get(acceptance_id, ("", {}))[1],
            "verification_command_ids",
            "command_ids",
        )
        if command_refs != task_refs_expected:
            _error(
                errors,
                "acceptance_command_set_mismatch",
                f"acceptance_matrix.{acceptance_id}.verification_command_ids",
                f"expected {sorted(task_refs_expected)}, got {sorted(command_refs)}",
            )

    for acceptance_id, (_task_id, criterion) in task_acceptance.items():
        command_refs = _refs(criterion, "verification_command_ids", "command_ids")
        _known_refs(
            errors,
            command_refs,
            set(task_commands),
            f"task_map.acceptance.{acceptance_id}.verification_command_ids",
            "command",
        )
    for command_id, (_task_id, command, _root) in task_commands.items():
        acceptance_refs = _refs(command, "acceptance_id", "acceptance_ids")
        _known_refs(
            errors,
            acceptance_refs,
            set(task_acceptance),
            f"task_map.commands.{command_id}.acceptance_ids",
            "acceptance",
        )
        expected = {
            acceptance_id
            for acceptance_id, (_owner, criterion) in task_acceptance.items()
            if command_id in _refs(
                criterion,
                "verification_command_ids",
                "command_ids",
            )
        }
        if acceptance_refs != expected:
            _error(
                errors,
                "command_acceptance_set_mismatch",
                f"task_map.commands.{command_id}.acceptance_ids",
                f"expected {sorted(expected)}, got {sorted(acceptance_refs)}",
            )

    test_body = ports.get("test_matrix")
    test_command_rows = _test_command_rows(test_body or {}, errors)
    test_commands_by_id = _unique_rows(
        test_command_rows,
        "test_matrix.commands",
        ("command_id", "id", "test_id"),
        errors,
    )
    if test_body is not None:
        _set_mismatch_errors(
            errors,
            left=set(task_commands),
            right=set(test_commands_by_id),
            left_name="task_map.commands",
            right_name="test_matrix.commands",
            code="command_registry_mismatch",
        )
    for command_id, row in test_commands_by_id.items():
        task_command = task_commands.get(command_id)
        if task_command is not None:
            expected_command = str(
                task_command[1].get("command") or task_command[1].get("cmd") or ""
            ).strip()
            actual_command = str(row.get("command") or row.get("cmd") or "").strip()
            if actual_command != expected_command:
                _error(
                    errors,
                    "command_definition_mismatch",
                    f"test_matrix.commands.{command_id}.command",
                    f"test command does not match task-map producer {command_id!r}",
                )
            expected_acceptance = _refs(
                task_command[1],
                "acceptance_id",
                "acceptance_ids",
            )
            actual_acceptance = _refs(row, "acceptance_id", "acceptance_ids")
            if actual_acceptance != expected_acceptance:
                _error(
                    errors,
                    "test_acceptance_set_mismatch",
                    f"test_matrix.commands.{command_id}.acceptance_ids",
                    f"expected {sorted(expected_acceptance)}, got {sorted(actual_acceptance)}",
                )

    test_rows = _rows(test_body or {}, "tests")
    tests_by_id = _unique_rows(
        test_rows,
        "test_matrix.tests",
        ("test_id", "id"),
        errors,
    )
    if test_body is not None:
        _test_reference_errors(
            errors,
            tests_by_id=tests_by_id,
            command_ids=set(test_commands_by_id),
            acceptance_ids=set(task_acceptance),
            has_command_registry=bool(_rows(test_body, "commands")),
        )

    real_e2e_body = ports.get("real_e2e_matrix")
    if real_e2e_body is not None:
        _real_e2e_matrix_errors(
            errors,
            body=real_e2e_body,
            task_acceptance=task_acceptance,
            task_commands=task_commands,
        )

    capability_body = ports.get("capability_matrix")
    capability_rows = _rows(capability_body or {}, "capabilities")
    capability_by_id = _unique_rows(
        capability_rows,
        "capability_matrix.capabilities",
        ("capability_id", "id"),
        errors,
    )
    if capability_body is not None:
        _capability_closure_errors(
            errors,
            capability_by_id=capability_by_id,
            task_by_id=task_by_id,
            acceptance_by_id=acceptance_by_id,
            command_by_id=test_commands_by_id,
            test_by_id=tests_by_id,
        )

    _source_index_extra_errors(errors, source_index, set(task_by_id))
    return errors


def _matrix_readiness_errors(
    name: str,
    body: Mapping[str, Any],
    errors: list[dict[str, str]],
) -> None:
    if str(body.get("status") or "").strip().lower() != "ready":
        _error(errors, "matrix_not_ready", f"plan_ports.{name}.status", "required matrix status must be ready")
    metadata = body.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    contract = metadata.get("enrichment_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    if str(contract.get("status") or "").strip().lower() != "fulfilled":
        _error(
            errors,
            "matrix_enrichment_unfulfilled",
            f"plan_ports.{name}.metadata.enrichment_contract.status",
            "required matrix enrichment contract must be fulfilled",
        )


def _capability_closure_errors(
    errors: list[dict[str, str]],
    *,
    capability_by_id: Mapping[str, Mapping[str, Any]],
    task_by_id: Mapping[str, Mapping[str, Any]],
    acceptance_by_id: Mapping[str, Mapping[str, Any]],
    command_by_id: Mapping[str, Mapping[str, Any]],
    test_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    capability_ids = set(capability_by_id)
    for task_id, row in task_by_id.items():
        _known_refs(errors, _refs(row, "capability_id", "capability_ids"), capability_ids, f"task_map.tasks.{task_id}.capability_ids", "capability")
    for acceptance_id, row in acceptance_by_id.items():
        refs = _refs(row, "capability_id", "capability_ids")
        reverse_refs = {
            capability_id
            for capability_id, capability in capability_by_id.items()
            if acceptance_id in _refs(
                capability,
                "acceptance_id",
                "acceptance_ids",
            )
        }
        _known_refs(errors, refs, capability_ids, f"acceptance_matrix.{acceptance_id}.capability_ids", "capability")
        if refs and reverse_refs and refs != reverse_refs:
            _error(
                errors,
                "acceptance_capability_set_mismatch",
                f"acceptance_matrix.{acceptance_id}.capability_ids",
                f"expected {sorted(reverse_refs)}, got {sorted(refs)}",
            )
        if not refs and not reverse_refs:
            _error(errors, "acceptance_capability_missing", f"acceptance_matrix.{acceptance_id}.capability_ids", "acceptance row must identify a capability")
    for test_id, row in test_by_id.items():
        _known_refs(errors, _refs(row, "capability_id", "capability_ids"), capability_ids, f"test_matrix.{test_id}.capability_ids", "capability")

    for capability_id, row in capability_by_id.items():
        tasks = _refs(row, "task_id", "task_ids", "owning_task_id", "owning_task_ids") | {
            task_id for task_id, task in task_by_id.items()
            if capability_id in _refs(task, "capability_id", "capability_ids")
        }
        acceptances = _refs(row, "acceptance_id", "acceptance_ids") | {
            acceptance_id for acceptance_id, acceptance in acceptance_by_id.items()
            if capability_id in _refs(acceptance, "capability_id", "capability_ids")
        }
        tests = _refs(row, "test_id", "test_ids") | {
            test_id for test_id, test in test_by_id.items()
            if capability_id in _refs(test, "capability_id", "capability_ids")
            or bool(_refs(test, "acceptance_id", "acceptance_ids") & acceptances)
        }
        commands = _refs(row, "command_id", "command_ids") | {
            command_id for command_id, command in command_by_id.items()
            if capability_id in _refs(command, "capability_id", "capability_ids")
            or bool(_refs(command, "acceptance_id", "acceptance_ids") & acceptances)
        }
        _known_refs(errors, tasks, set(task_by_id), f"capability_matrix.{capability_id}.task_ids", "task")
        _known_refs(errors, acceptances, set(acceptance_by_id), f"capability_matrix.{capability_id}.acceptance_ids", "acceptance")
        _known_refs(errors, tests, set(test_by_id), f"capability_matrix.{capability_id}.test_ids", "test")
        _known_refs(errors, commands, set(command_by_id), f"capability_matrix.{capability_id}.command_ids", "command")
        for kind, refs in (("task", tasks), ("acceptance", acceptances)):
            if not refs:
                _error(errors, f"capability_{kind}_missing", f"capability_matrix.{capability_id}.{kind}_ids", f"capability requires a linked {kind}")
        if not tests and not commands:
            _error(
                errors,
                "capability_test_missing",
                f"capability_matrix.{capability_id}.test_ids",
                "capability requires a linked test or command",
            )


def _source_index_extra_errors(
    errors: list[dict[str, str]],
    source_index: Mapping[str, Any],
    task_ids: set[str],
) -> None:
    rows = source_index.get("tasks")
    if isinstance(rows, Mapping):
        source_ids = {str(key).strip() for key in rows if str(key).strip()}
    else:
        source_ids = {
            _row_id(row, "task_id", "id")
            for row in _rows(source_index, "tasks")
        }
        source_ids.discard("")
    for task_id in sorted(source_ids - task_ids):
        _error(errors, "source_index_task_unknown", f"source_index.tasks.{task_id}", f"source index references unknown task {task_id!r}")


def _test_command_rows(
    body: Mapping[str, Any],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    for test in _rows(body, "tests"):
        direct = str(test.get("command") or test.get("cmd") or "").strip()
        commands = test.get("commands")
        embeds_commands = bool(
            direct
            or (
                isinstance(commands, list)
                and any(isinstance(item, Mapping) for item in commands)
            )
        )
        if embeds_commands:
            _error(
                errors,
                "test_command_registry_noncanonical",
                "test_matrix.tests",
                "Test Matrix has one command registry: define commands only in "
                "test_matrix.commands[] and reference their ids elsewhere",
            )
            break
    return [dict(row) for row in _rows(body, "commands")]


def _test_reference_errors(
    errors: list[dict[str, str]],
    *,
    tests_by_id: Mapping[str, Mapping[str, Any]],
    command_ids: set[str],
    acceptance_ids: set[str],
    has_command_registry: bool,
) -> None:
    if not has_command_registry:
        return
    for test_id, row in tests_by_id.items():
        field = f"test_matrix.tests.{test_id}"
        if str(row.get("command") or row.get("cmd") or "").strip():
            _error(
                errors,
                "test_command_definition_duplicate",
                f"{field}.command",
                "test rows must reference the canonical commands registry, not redefine command bodies",
            )
        raw_commands = row.get("commands")
        if isinstance(raw_commands, list) and any(
            isinstance(item, Mapping) for item in raw_commands
        ):
            _error(
                errors,
                "test_command_definition_duplicate",
                f"{field}.commands",
                "test rows must reference command ids, not embed command objects",
            )
        refs = _refs(row, "command_id", "command_ids")
        if isinstance(raw_commands, str):
            refs.add(raw_commands.strip())
        elif isinstance(raw_commands, list):
            refs.update(
                str(item).strip()
                for item in raw_commands
                if isinstance(item, str) and item.strip()
            )
        if not refs:
            _error(
                errors,
                "test_command_ref_missing",
                f"{field}.command_ids",
                "test row must reference at least one canonical command id",
            )
        _known_refs(errors, refs, command_ids, f"{field}.command_ids", "command")
        _known_refs(
            errors,
            _refs(row, "acceptance_id", "acceptance_ids"),
            acceptance_ids,
            f"{field}.acceptance_ids",
            "acceptance",
        )


def _real_e2e_matrix_errors(
    errors: list[dict[str, str]],
    *,
    body: Mapping[str, Any],
    task_acceptance: Mapping[str, tuple[str, Mapping[str, Any]]],
    task_commands: Mapping[str, tuple[str, Mapping[str, Any], str]],
) -> None:
    rows = _rows(body, "rows")
    acceptance_ids = set(task_acceptance)
    for index, row in enumerate(rows):
        row_id = _row_id(row, "id") or str(index)
        field = f"real_e2e_matrix.rows[{row_id}]"
        acceptance_refs = _refs(row, "acceptance_id", "acceptance_ids")
        _known_refs(
            errors,
            acceptance_refs,
            acceptance_ids,
            f"{field}.acceptance_ids",
            "acceptance",
        )
        command_id = str(row.get("command_id") or "").strip()
        command_required = row.get("command_required")
        execution_mode = str(row.get("execution_mode") or "").strip()

        if execution_mode == "immutable_baseline_only":
            _immutable_e2e_baseline_errors(errors, row=row, field=field)
            continue
        if not command_id:
            continue
        task_command = task_commands.get(command_id)
        if task_command is None:
            _error(
                errors,
                "real_e2e_command_ref_unknown",
                f"{field}.command_id",
                f"unknown canonical command {command_id!r}",
            )
            continue
        canonical = task_command[1]
        tier = str(canonical.get("tier") or "").strip()
        if tier not in {"e2e", "real_e2e", "manual_evidence"}:
            _error(
                errors,
                "real_e2e_command_tier_invalid",
                f"{field}.command_id",
                f"command {command_id!r} has tier {tier!r}; runtime/static "
                "aggregation is not real E2E evidence",
            )
        canonical_acceptance = _refs(
            canonical,
            "acceptance_id",
            "acceptance_ids",
        )
        if acceptance_refs != canonical_acceptance:
            _error(
                errors,
                "real_e2e_acceptance_set_mismatch",
                f"{field}.acceptance_ids",
                f"expected {sorted(canonical_acceptance)}, "
                f"got {sorted(acceptance_refs)}",
            )
        row_command = str(row.get("command") or row.get("cmd") or "").strip()
        canonical_command = str(
            canonical.get("command") or canonical.get("cmd") or ""
        ).strip()
        if row_command and row_command != canonical_command:
            _error(
                errors,
                "real_e2e_command_definition_mismatch",
                f"{field}.command",
                f"real-E2E row does not match canonical command {command_id!r}",
            )
        if command_required is False:
            _error(
                errors,
                "real_e2e_command_requirement_invalid",
                f"{field}.command_required",
                "a command-backed real-E2E row cannot disable command execution",
            )

    for acceptance_id, (_task_id, criterion) in task_acceptance.items():
        if str(criterion.get("evidence_mode") or "") != "immutable_baseline_only":
            continue
        matching = [
            row
            for row in rows
            if acceptance_id in _refs(row, "acceptance_id", "acceptance_ids")
            and str(row.get("execution_mode") or "")
            == "immutable_baseline_only"
        ]
        if not matching:
            _error(
                errors,
                "immutable_baseline_real_e2e_row_missing",
                f"real_e2e_matrix.acceptance[{acceptance_id}]",
                "immutable E2E criterion requires a matching immutable baseline row",
            )


def _immutable_e2e_baseline_errors(
    errors: list[dict[str, str]],
    *,
    row: Mapping[str, Any],
    field: str,
) -> None:
    if row.get("command_required") is not False:
        _error(
            errors,
            "immutable_baseline_command_requirement_invalid",
            f"{field}.command_required",
            "immutable baseline row must set command_required=false",
        )
    if str(row.get("command_id") or row.get("command") or row.get("cmd") or "").strip():
        _error(
            errors,
            "immutable_baseline_command_present",
            f"{field}.command_id",
            "immutable baseline row must not redispatch or relabel a command",
        )
    if not str(row.get("origin_command") or "").strip():
        _error(
            errors,
            "immutable_baseline_origin_command_missing",
            f"{field}.origin_command",
            "immutable baseline row requires the exact origin command",
        )
    target_commit = str(row.get("target_commit") or "").strip().lower()
    if not _is_commit_digest(target_commit):
        _error(
            errors,
            "immutable_baseline_target_commit_invalid",
            f"{field}.target_commit",
            "immutable baseline row requires a full target commit digest",
        )
    evidence_refs = {
        str(value).strip()
        for value in row.get("evidence_refs", [])
        if str(value).strip()
    }
    if target_commit and f"git:{target_commit}" not in {
        value.lower() for value in evidence_refs
    }:
        _error(
            errors,
            "immutable_baseline_commit_ref_missing",
            f"{field}.evidence_refs",
            "immutable baseline evidence must include git:<target_commit>",
        )
    if not any(value.startswith("artifacts/") for value in evidence_refs):
        _error(
            errors,
            "immutable_baseline_artifact_ref_missing",
            f"{field}.evidence_refs",
            "immutable baseline evidence must include retained artifact refs",
        )


def _is_commit_digest(value: str) -> bool:
    return len(value) in {40, 64} and all(
        character in "0123456789abcdef" for character in value
    )


def _task_command_rows(task: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    validation = task.get("validation")
    commands = validation.get("commands") if isinstance(validation, Mapping) else None
    return [row for row in commands or [] if isinstance(row, Mapping)]


def _command_root(
    command: Mapping[str, Any],
    task: Mapping[str, Any],
    task_map: Mapping[str, Any],
) -> str:
    for source in (command, task):
        for key in ("execution_root", "run_cwd", "cwd", "working_directory"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    conventions = task_map.get("shared_conventions")
    conventions = conventions if isinstance(conventions, Mapping) else {}
    for key in ("run_cwd", "package_root", "target_root"):
        value = str(conventions.get(key) or "").strip()
        if value:
            return value
    return str(task_map.get("target_root") or ".").strip() or "."


def _safe_execution_root(value: str) -> bool:
    if not value or "\n" in value or "\r" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _unique_rows(
    rows: list[Mapping[str, Any]],
    field: str,
    id_keys: tuple[str, ...],
    errors: list[dict[str, str]],
) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        row_id = _row_id(row, *id_keys)
        if not row_id:
            _error(errors, f"{field.replace('.', '_')}_id_missing", f"{field}[{index}]", "row requires a stable id")
        elif row_id in out:
            _error(errors, f"{field.replace('.', '_')}_id_duplicate", f"{field}[{index}]", f"duplicate id {row_id!r}")
        else:
            out[row_id] = row
    return out


def _set_mismatch_errors(
    errors: list[dict[str, str]],
    *,
    left: set[str],
    right: set[str],
    left_name: str,
    right_name: str,
    code: str,
) -> None:
    for value in sorted(left - right):
        _error(errors, code, left_name, f"{value!r} is missing from {right_name}")
    for value in sorted(right - left):
        _error(errors, code, right_name, f"{value!r} is missing from {left_name}")


def _known_refs(
    errors: list[dict[str, str]],
    refs: set[str],
    known: set[str],
    field: str,
    kind: str,
) -> None:
    for value in sorted(refs - known):
        _error(errors, f"{kind}_ref_unknown", field, f"unknown {kind} id {value!r}")


def _rows(body: Mapping[str, Any], *keys: str) -> list[Mapping[str, Any]]:
    for key in keys:
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
    return []


def _row_id(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _refs(row: Mapping[str, Any], *keys: str) -> set[str]:
    out: set[str] = set()
    for key in keys:
        value = row.get(key)
        values = value if isinstance(value, list | tuple | set) else [value]
        out.update(str(item).strip() for item in values if str(item or "").strip())
    return out


def _error(
    errors: list[dict[str, str]],
    code: str,
    field: str,
    message: str,
) -> None:
    errors.append({"code": code, "field": field, "message": message})


__all__ = ["plan_matrix_admission_errors"]
