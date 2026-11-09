"""Carry explicitly authored optional task declarations through gap serialization."""

from copy import deepcopy
from typing import Any, Mapping


def preserve_gap_task_declarations(item: dict[str, Any], raw: Mapping[str, Any]) -> None:
    """Preserve supplied content without inferring scope or inheriting old semantics.

    The normal task-map and writer admission paths still validate these values.
    Explicit empty or invalid declarations must not silently become absent.
    """
    for field in (
        "description", "source_excerpt", "source_ref", "owner_decision_ids",
        "lane_affinity", "skills_required", "explicit_non_goals", "exclusive_files",
    ):
        if field in raw:
            item[field] = deepcopy(raw[field])
    if isinstance(raw.get("verification"), dict):
        item["verification"] = deepcopy(raw["verification"])

    root_owner_class = str(raw.get("root_owner_class") or "").strip()
    if root_owner_class:
        item["root_owner_class"] = root_owner_class
    base_commit = str(raw.get("base_commit") or "").strip()
    if base_commit:
        item["base_commit"] = base_commit


def _acceptance(raw: dict[str, Any]) -> list[str]:
    values = raw.get("acceptance") or raw.get("acceptance_criteria")
    if not isinstance(values, list):
        return _string_list(values)
    return _string_list([
        item.get("statement") or item.get("text") or item.get("criterion")
        or item.get("description") or item.get("acceptance") or item.get("title") or ""
        if isinstance(item, dict) else item
        for item in values
    ])


def _verification(raw: dict[str, Any]) -> list[str]:
    source = raw.get("verify_commands") or raw.get("verification")
    if isinstance(source, dict):
        source = source.get("commands")
    if isinstance(source, list):
        source = [
            row.get("command") or row.get("cmd") or "" if isinstance(row, dict) else row
            for row in source
        ]
    commands = _string_list(source)
    if commands:
        return commands
    validation = raw.get("validation")
    rows = validation.get("commands") if isinstance(validation, dict) else []
    if not isinstance(rows, list):
        return []
    return [
        command
        for row in rows
        if isinstance(row, dict)
        and (command := str(row.get("command") or "").strip())
    ]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
