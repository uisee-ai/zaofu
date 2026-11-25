"""Assembly-owner topology checks for executable task maps."""

from __future__ import annotations

from typing import Any

from zf.runtime.writer_task_materialization import (
    TASK_PIPELINE_ENTRY_VERIFY_ONLY,
    writer_task_evidence_contract,
    writer_task_pipeline_entry_mode,
)


def read_only_verification_ids(tasks: list[dict[str, Any]]) -> set[str]:
    """Exclude only verified read-only obligations from the writer topology."""
    readonly = set()
    for task in tasks:
        if task.get("allowed_paths") != [] or task.get("exclusive_files") or task.get("scope"):
            continue
        try:
            evidence = writer_task_evidence_contract(task)
            if (evidence.get("write_free") is True
                    and writer_task_pipeline_entry_mode(task, task) == TASK_PIPELINE_ENTRY_VERIFY_ONLY):
                readonly.add(str(task.get("task_id") or task.get("id") or "").strip())
        except ValueError:
            # Invalid or contradictory evidence cannot remove a writer; admission reports it.
            continue
    return readonly


def assembly_ownership_errors(
    *,
    assembly_owner_roles: dict[str, str],
    bundle_owner_roles: dict[str, str],
    task_dependencies: dict[str, list[str]],
    read_only_task_ids: set[str],
) -> list[str]:
    """Reject parallel bundles without an independent assembly owner."""

    bundle_tasks = [(task, owner) for task, owner in bundle_owner_roles.items() if task not in read_only_task_ids]
    has_parallel_bundles = any(
        left_owner != right_owner
        and not _task_depends_on(
            left_task,
            right_task,
            task_dependencies=task_dependencies,
        )
        and not _task_depends_on(
            right_task,
            left_task,
            task_dependencies=task_dependencies,
        )
        for index, (left_task, left_owner) in enumerate(bundle_tasks)
        for right_task, right_owner in bundle_tasks[index + 1:]
    )
    if not has_parallel_bundles and not assembly_owner_roles:
        return []
    if has_parallel_bundles and not assembly_owner_roles:
        return [
            "缺 assembly 任务: 多个并行 bundle 需要一个独立 "
            "root_owner_class=assembly 任务"
        ]
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
            bundle_owner_roles[dependency_id]
            for dependency_id in reachable
            if dependency_id in bundle_owner_roles
        }
        if owner_role in dependency_owner_roles:
            errors.append(
                f"{task_id}.owner_role {owner_role!r} 与并行 bundle owner "
                "冲突: assembly 任务不能和它依赖的 bundle 共用同一 "
                "owner_role(自锁)"
            )
    return errors


def _task_depends_on(
    task_id: str,
    dependency_id: str,
    *,
    task_dependencies: dict[str, list[str]],
) -> bool:
    pending = list(task_dependencies.get(task_id, []))
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == dependency_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(task_dependencies.get(current, []))
    return False
