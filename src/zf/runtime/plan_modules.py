"""Explicit module relationships in the existing Task Map, without task copies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def plan_module_errors(task_map: Mapping[str, Any]) -> list[str]:
    """Check references and containment only; planners own module semantics."""
    if "modules" not in task_map:
        return []
    modules = task_map["modules"]
    if not isinstance(modules, list):
        return ["modules must be an array when present"]
    tasks = {
        str(row.get("task_id") or row.get("id") or "")
        for row in task_map.get("tasks", []) if isinstance(row, Mapping)
    }
    claims = {
        str(row.get("goal_claim_id") or row.get("id") or "")
        for row in task_map.get("goal_claims", []) if isinstance(row, Mapping)
    }
    errors: list[str] = []
    parents: dict[str, str] = {}
    for index, row in enumerate(modules):
        prefix = f"modules[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        module_id = row.get("module_id")
        if not isinstance(module_id, str) or not module_id.strip():
            errors.append(f"{prefix}.module_id must be a nonempty string")
            continue
        if module_id in parents:
            errors.append(f"{prefix}.module_id duplicates {module_id!r}")
        parent = row.get("parent_module_id", "")
        if not isinstance(parent, str):
            errors.append(f"{prefix}.parent_module_id must be a string")
            parent = ""
        parents[module_id] = parent
        if not isinstance(row.get("title"), str) or not row["title"].strip():
            errors.append(f"{prefix}.title must be a nonempty string")
        for field in ("task_ids", "goal_claim_ids", "source_refs", "design_refs"):
            values = row.get(field, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                errors.append(f"{prefix}.{field} must contain nonempty strings")
                continue
            if len(values) != len(set(values)):
                errors.append(f"{prefix}.{field} contains duplicate references")
            known = tasks if field == "task_ids" else claims
            if field == "task_ids" or (field == "goal_claim_ids" and claims):
                for value in values:
                    if value not in known:
                        errors.append(f"{prefix}.{field} references unknown id {value!r}")
    for module_id, parent in parents.items():
        if parent and parent not in parents:
            errors.append(f"module {module_id!r} references unknown parent {parent!r}")
        seen = {module_id}
        while parent in parents:
            if parent in seen:
                errors.append(f"module {module_id!r} has cyclic containment")
                break
            seen.add(parent)
            parent = parents[parent]
    return errors


def plan_modules(task_map: Mapping[str, Any]) -> dict[str, Any]:
    """Read declared modules; missing legacy information remains explicit."""
    errors = plan_module_errors(task_map)
    if errors:
        return {"source": "invalid", "modules": [], "diagnostics": errors}
    rows = []
    for row in task_map.get("modules", []):
        rows.append({
            "module_id": row["module_id"], "title": row["title"],
            "parent_module_id": row.get("parent_module_id", ""),
            **{key: list(row.get(key, [])) for key in (
                "task_ids", "goal_claim_ids", "source_refs", "design_refs",
            )},
        })
    return {
        "source": "task_map.modules" if rows else "unavailable",
        "modules": rows,
        "diagnostics": [] if rows else ["本版计划未声明模块分解；任务仍可单独查看"],
    }
