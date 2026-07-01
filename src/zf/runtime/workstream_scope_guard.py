"""WorkstreamScopeGuard — doc 64 §6 pre-promotion path overlap check.

When a Channel/Squad promotes a synthesis into a real workflow via
``workflow.invoke.requested``, the orchestrator must refuse to accept the
invocation when the declared paths overlap with files already claimed by
an in-flight task's ``contract.exclusive_files``. This prevents two
parallel workstreams from racing on the same file.

This module is the pure overlap check, separated from the reactor so it
can be unit tested in the slice (see L3 step 4 of the channel test plan).

The detection rule mirrors ``_check_fanout_independence`` in
``orchestrator_dispatch.py`` (pairwise exclusive ∩ exclusive), but
operates on a single proposed path set against the live task store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from zf.core.task.store import TaskStore


# Statuses that count as "in-flight" for scope reservation. Terminal
# tasks (done, cancelled) no longer reserve files. Backlog tasks have
# no committed work yet but their contracts are already declared; we
# treat them as reserved to match the task_map.py:111 semantics.
IN_FLIGHT_STATUSES = frozenset({
    "backlog",
    "todo",
    "in_progress",
    "blocked",
    "review",
    "testing",
})


@dataclass(frozen=True)
class ScopeOverlap:
    task_id: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class ScopeCheckResult:
    allowed: bool
    overlaps: list[ScopeOverlap] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if self.allowed:
            return ""
        parts = [
            f"{overlap.task_id}:{sorted(overlap.paths)}"
            for overlap in self.overlaps
        ]
        return "exclusive_files overlap: " + ", ".join(parts)


def check_workstream_scope(
    state_dir: Path,
    proposed_paths: list[str] | tuple[str, ...] | set[str],
    proposed_task_id: str = "",
) -> ScopeCheckResult:
    """Scan in-flight tasks for ``exclusive_files`` overlap with ``proposed_paths``.

    Returns ``ScopeCheckResult(allowed=True)`` when the proposal is
    clear. On overlap, ``allowed=False`` and ``overlaps`` lists each
    offending task with the colliding file paths.

    ``proposed_task_id`` lets the caller pass the id of the task the
    invocation is operating on — that task's own exclusive_files claim
    is ignored (self-overlap is not a conflict).

    The caller passes the live ``state_dir`` (e.g. project ``.zf``);
    the kanban is loaded fresh on each call so the check sees the
    latest reservation set.
    """
    proposed = {str(p).strip() for p in (proposed_paths or []) if str(p).strip()}
    if not proposed:
        return ScopeCheckResult(allowed=True)

    kanban_path = Path(state_dir) / "kanban.json"
    if not kanban_path.exists():
        return ScopeCheckResult(allowed=True)

    store = TaskStore(kanban_path)
    overlaps: list[ScopeOverlap] = []
    for task in store.list_all():
        if task.id == proposed_task_id:
            continue
        if task.status not in IN_FLIGHT_STATUSES:
            continue
        contract = getattr(task, "contract", None)
        if contract is None:
            continue
        reserved = {
            str(p).strip()
            for p in (getattr(contract, "exclusive_files", []) or [])
            if str(p).strip()
        }
        if not reserved:
            continue
        clash = proposed & reserved
        if clash:
            overlaps.append(ScopeOverlap(
                task_id=task.id,
                paths=tuple(sorted(clash)),
            ))

    return ScopeCheckResult(
        allowed=not overlaps,
        overlaps=overlaps,
    )
