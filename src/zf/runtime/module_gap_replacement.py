"""Mechanical graph transforms for task-map gap replacements."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


def select_successor_base_commit(payload: dict[str, Any]) -> str:
    """Return the first trusted immutable target carried by a gap envelope."""

    for key in (
        "target_commit",
        "target_ref",
        "base_commit",
        "dispatch_base_commit",
        "candidate_head_commit",
        "candidate_base_commit",
        "source_commit",
    ):
        value = str(payload.get(key) or "").strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
            return value
    return ""


def completed_task_ref_dependencies(
    *,
    state_dir: Path,
    project_root: Path | None,
    task_ids: list[str],
    target_commit: str,
    workflow_run_id: str = "",
) -> list[str]:
    """Return dependency TaskRefs already represented by an immutable target."""

    if project_root is None or not _valid_commit(target_commit):
        return []
    index_path = state_dir / "refs" / "task-index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(index, dict):
        return []

    projected = _candidate_projection_commits(state_dir)
    completed: list[str] = []
    for task_id in _unique_strings(task_ids):
        entry = index.get(task_id)
        if not isinstance(entry, dict):
            continue
        trace_id = str(entry.get("trace_id") or "").strip()
        if workflow_run_id and trace_id and trace_id != workflow_run_id:
            continue
        source_commit = str(entry.get("source_commit") or "").strip()
        candidate_commits = [
            source_commit,
            *projected.get((task_id, source_commit), []),
        ]
        if any(
            _valid_commit(commit)
            and _git_is_ancestor(project_root, commit, target_commit)
            for commit in candidate_commits
        ):
            completed.append(task_id)
    return completed


def bind_replacement_group(
    gap_tasks: list[dict[str, Any]],
    *,
    envelope_supersedes: list[str],
    successor_base_commit: str,
) -> list[dict[str, Any]]:
    """Bind an envelope-level replacement to one unambiguous terminal task."""

    tasks = [dict(task) for task in gap_tasks]
    declared = set(_superseded_task_ids(tasks))
    unresolved = [item for item in envelope_supersedes if item not in declared]
    if unresolved:
        terminal_ids = _gap_terminal_task_ids(tasks)
        if len(tasks) == 1:
            terminal_ids = [_task_id(tasks[0])]
        if len(terminal_ids) != 1:
            raise ValueError(
                "multi-task replacement requires one terminal gap task; "
                "declare blocked_by between predecessor tasks and the terminal successor"
            )
        terminal_id = terminal_ids[0]
        for task in tasks:
            if _task_id(task) != terminal_id:
                continue
            task["supersedes_task_ids"] = _unique_strings([
                *_string_list(task.get("supersedes_task_ids")),
                *unresolved,
            ])
            break

    trusted_base = str(successor_base_commit or "").strip()
    trusted_base_valid = bool(
        re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", trusted_base)
    )
    for task in tasks:
        if not _string_list(task.get("supersedes_task_ids")):
            continue
        base_commit = str(task.get("base_commit") or "").strip()
        if not base_commit and trusted_base_valid:
            base_commit = trusted_base
            task["base_commit"] = base_commit
        if base_commit:
            refs = _string_list(task.get("source_refs"))
            git_ref = f"git:{base_commit}"
            if git_ref not in refs:
                refs.append(git_ref)
            task["source_refs"] = refs
    return tasks


def inherit_superseded_incoming_dependencies(
    gap_tasks: list[dict[str, Any]],
    *,
    base_tasks_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace a task node without dropping its incoming dependency edges."""

    tasks = [dict(task) for task in gap_tasks]
    by_id = {_task_id(task): task for task in tasks if _task_id(task)}
    gap_ids = set(by_id)
    # When a replacement chain contains multiple successors, dependencies on
    # any task being replaced are rewired by the successor graph below.  Do
    # not inherit those stale edges from another superseded parent (for
    # example, an assembly task depending on the implementation task it is
    # replacing alongside a dedicated implementation successor).
    all_superseded = {
        old_id
        for task in tasks
        for old_id in _string_list(task.get("supersedes_task_ids"))
    }
    for successor in tasks:
        superseded = _string_list(successor.get("supersedes_task_ids"))
        if not superseded:
            continue
        component = _gap_dependency_ancestors(
            _task_id(successor),
            by_id=by_id,
        )
        roots = [
            task_id
            for task_id in component
            if not (set(_task_dependencies(by_id[task_id])) & component)
        ]
        inherited = _unique_strings(
            dependency
            for old_id in superseded
            for dependency in _task_dependencies(base_tasks_by_id.get(old_id, {}))
            if dependency not in gap_ids and dependency not in all_superseded
        )
        for root_id in roots:
            root = by_id[root_id]
            root["blocked_by"] = _unique_strings([
                *_task_dependencies(root),
                *inherited,
            ])
            root.pop("dependencies", None)
    return tasks


def rewire_superseded_dependents(
    tasks: list[dict[str, Any]],
    *,
    gap_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    replacements_by_old: dict[str, list[str]] = {}
    for task in gap_tasks:
        successor_id = _task_id(task)
        for old_id in _string_list(task.get("supersedes_task_ids")):
            successors = replacements_by_old.setdefault(old_id, [])
            if successor_id and successor_id not in successors:
                successors.append(successor_id)
    out: list[dict[str, Any]] = []
    for raw in tasks:
        item = dict(raw)
        dependencies = _unique_strings(
            successor
            for dependency in _task_dependencies(item)
            for successor in replacements_by_old.get(dependency, [dependency])
        )
        if dependencies or "blocked_by" in item:
            item["blocked_by"] = dependencies
            item.pop("dependencies", None)
        out.append(item)
    return out


def raise_dependency_waves(tasks: list[dict[str, Any]]) -> None:
    """Keep existing waves, raising downstream tasks after inserted successors."""

    by_id = {_task_id(task): task for task in tasks if _task_id(task)}
    for _ in range(len(tasks)):
        changed = False
        for task in tasks:
            dependencies = [
                by_id[dependency]
                for dependency in _task_dependencies(task)
                if dependency in by_id
            ]
            if not dependencies:
                continue
            wave = _int_value(task.get("wave"), default=0)
            required = max(
                _int_value(dependency.get("wave"), default=0) + 1
                for dependency in dependencies
            )
            if required > wave:
                task["wave"] = required
                changed = True
        if not changed:
            return


def _gap_terminal_task_ids(gap_tasks: list[dict[str, Any]]) -> list[str]:
    gap_ids = {_task_id(task) for task in gap_tasks if _task_id(task)}
    referenced = {
        dependency
        for task in gap_tasks
        for dependency in _task_dependencies(task)
        if dependency in gap_ids
    }
    return [
        _task_id(task)
        for task in gap_tasks
        if _task_id(task) and _task_id(task) not in referenced
    ]


def _gap_dependency_ancestors(
    task_id: str,
    *,
    by_id: dict[str, dict[str, Any]],
) -> set[str]:
    pending = [task_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen or current not in by_id:
            continue
        seen.add(current)
        pending.extend(
            dependency
            for dependency in _task_dependencies(by_id[current])
            if dependency in by_id
        )
    return seen


def _task_dependencies(raw: dict[str, Any]) -> list[str]:
    return _unique_strings([
        *_string_list(raw.get("blocked_by")),
        *_string_list(raw.get("dependencies")),
    ])


def _superseded_task_ids(gap_tasks: list[dict[str, Any]]) -> list[str]:
    return _unique_strings(
        value
        for task in gap_tasks
        for container in (task, task.get("payload"))
        if isinstance(container, dict)
        for value in _string_list(container.get("supersedes_task_ids"))
    )


def _task_id(raw: dict[str, Any]) -> str:
    return str(raw.get("task_id") or raw.get("id") or "").strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _unique_strings(values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _int_value(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _candidate_projection_commits(state_dir: Path) -> dict[tuple[str, str], list[str]]:
    commits: dict[tuple[str, str], list[str]] = {}
    events_path = state_dir / "events.jsonl"
    try:
        handle = events_path.open(encoding="utf-8")
    except OSError:
        return commits
    with handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                not isinstance(event, dict)
                or event.get("type") != "candidate.task_ref.applied"
            ):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            task_id = str(event.get("task_id") or payload.get("task_id") or "").strip()
            source_commit = str(payload.get("source_commit") or "").strip()
            candidate_commit = str(payload.get("commit") or "").strip()
            if task_id and _valid_commit(source_commit) and _valid_commit(candidate_commit):
                commits.setdefault((task_id, source_commit), []).append(candidate_commit)
    return commits


def _valid_commit(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value))


def _git_is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
