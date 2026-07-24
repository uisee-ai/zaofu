"""Mechanical DAG and evidence-producer checks for Task Map admission."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any


def stable_topological_order(task_map: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    tasks = [
        item for item in task_map.get("tasks", [])
        if isinstance(item, Mapping)
    ]
    ordered_ids = [
        str(item.get("task_id") or item.get("id") or "").strip()
        for item in tasks
    ]
    ordered_ids = [item for item in ordered_ids if item]
    index = {task_id: position for position, task_id in enumerate(ordered_ids)}
    dependencies = {
        str(item.get("task_id") or item.get("id") or "").strip(): _strings(
            item.get("blocked_by")
        )
        for item in tasks
        if str(item.get("task_id") or item.get("id") or "").strip()
    }
    errors: list[str] = []
    for task_id, deps in dependencies.items():
        if task_id in deps:
            errors.append(f"{task_id}.blocked_by contains a self-cycle")
    indegree = {
        task_id: sum(1 for dep in deps if dep in dependencies and dep != task_id)
        for task_id, deps in dependencies.items()
    }
    dependents: dict[str, list[str]] = {task_id: [] for task_id in dependencies}
    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep in dependents and dep != task_id:
                dependents[dep].append(task_id)
    ready = sorted(
        (task_id for task_id, degree in indegree.items() if degree == 0),
        key=lambda task_id: index[task_id],
    )
    result: list[str] = []
    while ready:
        task_id = ready.pop(0)
        result.append(task_id)
        for dependent in sorted(
            dependents[task_id],
            key=lambda item: index[item],
        ):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=lambda item: index[item])
    cyclic = [task_id for task_id in ordered_ids if task_id not in result]
    if cyclic:
        errors.append("task dependency cycle: " + " -> ".join(cyclic))
    return result, errors


def evidence_producibility_errors(task_map: Mapping[str, Any]) -> list[str]:
    tasks = {
        str(item.get("task_id") or item.get("id") or "").strip(): item
        for item in task_map.get("tasks", [])
        if isinstance(item, Mapping)
        and str(item.get("task_id") or item.get("id") or "").strip()
    }
    dependencies = {
        task_id: set(_strings(task.get("blocked_by")))
        for task_id, task in tasks.items()
    }
    errors: list[str] = []
    for task_id, task in tasks.items():
        command_ids = _verification_command_ids(task)
        criteria = task.get("acceptance_criteria")
        if not isinstance(criteria, list):
            criteria = task.get("acceptance")
        for index, criterion in enumerate(
            criteria if isinstance(criteria, list) else []
        ):
            if not isinstance(criterion, Mapping):
                continue
            acceptance_id = str(
                criterion.get("acceptance_id")
                or criterion.get("id")
                or f"AC{index + 1}"
            )
            for command_id in _strings(criterion.get("verification_command_ids")):
                if command_id not in command_ids:
                    errors.append(
                        f"{task_id}.{acceptance_id} references unknown "
                        f"verification command {command_id!r}"
                    )
            requirements = criterion.get("evidence_requirements")
            for evidence in (
                requirements if isinstance(requirements, list) else []
            ):
                if not isinstance(evidence, Mapping):
                    continue
                producer_id = str(evidence.get("producer_task_id") or "")
                if producer_id not in tasks:
                    errors.append(
                        f"{task_id}.{acceptance_id} evidence producer "
                        f"{producer_id!r} does not exist"
                    )
                    continue
                producer = tasks[producer_id]
                producer_paths = _strings(evidence.get("producer_paths"))
                owned_paths = [
                    *_strings(producer.get("allowed_paths")),
                    *_strings(producer.get("exclusive_files")),
                    *_strings(producer.get("shared_files")),
                ]
                for path in producer_paths:
                    if not any(_path_owned(path, pattern) for pattern in owned_paths):
                        errors.append(
                            f"{task_id}.{acceptance_id} producer {producer_id!r} "
                            f"does not own evidence path {path!r}"
                        )
                if bool(evidence.get("independence_required")):
                    verifier = str(
                        criterion.get("verification_owner")
                        or task.get("verification_owner")
                        or task.get("owner_role")
                        or ""
                    )
                    producer_owner = str(producer.get("owner_role") or "")
                    if verifier and producer_owner and verifier == producer_owner:
                        errors.append(
                            f"{task_id}.{acceptance_id} independent verifier "
                            f"collides with producer owner {producer_owner!r}"
                        )
                if producer_id != task_id and producer_id not in _dependency_closure(
                    task_id,
                    dependencies,
                ):
                    errors.append(
                        f"{task_id}.{acceptance_id} does not depend on evidence "
                        f"producer {producer_id!r}"
                    )
    return errors


def validate_task_map_evidence(
    task_map: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    order, errors = stable_topological_order(task_map)
    return order, [*errors, *evidence_producibility_errors(task_map)]


def _verification_command_ids(task: Mapping[str, Any]) -> set[str]:
    rows: list[Any] = []
    for value in (
        task.get("verification_commands"),
        task.get("verify_commands"),
        (
            task.get("validation", {}).get("commands")
            if isinstance(task.get("validation"), Mapping)
            else None
        ),
    ):
        if isinstance(value, list):
            rows.extend(value)
        elif isinstance(value, Mapping):
            rows.extend(value.get("commands") or [])
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if isinstance(row, Mapping):
            command_id = str(row.get("command_id") or row.get("id") or "")
            if command_id:
                ids.add(command_id)
        elif str(row).strip():
            ids.add(f"command-{index + 1}")
    return ids


def _dependency_closure(
    task_id: str,
    dependencies: Mapping[str, set[str]],
) -> set[str]:
    result: set[str] = set()
    pending = list(dependencies.get(task_id, set()))
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(dependencies.get(current, set()))
    return result


def _path_owned(path: str, pattern: str) -> bool:
    normalized_path = path.strip().lstrip("./")
    normalized_pattern = pattern.strip().lstrip("./")
    if not normalized_path or not normalized_pattern:
        return False
    return (
        fnmatch.fnmatch(normalized_path, normalized_pattern)
        or normalized_path == normalized_pattern.rstrip("/")
        or normalized_path.startswith(normalized_pattern.rstrip("/**") + "/")
    )


def _strings(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


__all__ = ["evidence_producibility_errors", "stable_topological_order"]
