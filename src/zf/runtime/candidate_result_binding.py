"""Strictly bind task-level results to an integrated candidate."""

from __future__ import annotations

from collections.abc import Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id, resolve_run_id, run_aliases


def same_task_map_generation(left: str, right: str) -> bool:
    """Match the full and shortened encodings of one task-map generation."""

    a = str(left or "").strip().removeprefix("task-map-")
    b = str(right or "").strip().removeprefix("task-map-")
    if not a or not b:
        return a == b
    if len(a) < 12 or len(b) < 12:
        return a == b
    return a.startswith(b) or b.startswith(a)


def candidate_task_source_commits(
    events: Sequence[ZfEvent],
    *,
    workflow_run_id: str,
    candidate_head_commit: str,
) -> dict[str, str]:
    """Return exact task commits integrated into the current candidate.

    Candidate integration may replay a task commit and therefore produce a
    different candidate SHA with the same change. Incremental candidates also
    inherit tasks from their candidate base. A task result is bound only when
    that lineage lists the task and the latest same-run task ref recorded
    before the current candidate points to its exact target.
    """

    aliases = run_aliases(events)
    canonical_run_id = resolve_run_id(events, workflow_run_id)
    candidate_index = -1
    completed_task_ids: set[str] = set()
    candidate_base_by_head: dict[str, tuple[str, set[str]]] = {}
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.type != "candidate.ready" or not isinstance(event.payload, dict):
            continue
        body = event.payload
        event_run = event_run_id(event, aliases=aliases)
        if canonical_run_id and event_run != canonical_run_id:
            continue
        head = str(body.get("candidate_head_commit") or "").strip()
        if not head or head in candidate_base_by_head:
            continue
        candidate_base_by_head[head] = (
            str(body.get("candidate_base_commit") or "").strip(),
            {
                str(item).strip()
                for item in body.get("completed_task_ids") or []
                if str(item).strip()
            },
        )
        if head == candidate_head_commit and candidate_index < 0:
            candidate_index = index

    lineage_head = candidate_head_commit
    visited: set[str] = set()
    while lineage_head and lineage_head not in visited:
        visited.add(lineage_head)
        lineage = candidate_base_by_head.get(lineage_head)
        if lineage is None:
            break
        lineage_head, task_ids = lineage
        completed_task_ids.update(task_ids)
    if candidate_index < 0 or not completed_task_ids:
        return {}

    commits: dict[str, str] = {}
    for event in events[: candidate_index + 1]:
        if event.type != "task.ref.updated" or not isinstance(event.payload, dict):
            continue
        body = event.payload
        task_id = str(event.task_id or body.get("task_id") or "").strip()
        if task_id not in completed_task_ids:
            continue
        event_run = event_run_id(event, aliases=aliases)
        if canonical_run_id and event_run != canonical_run_id:
            continue
        source_commit = str(body.get("source_commit") or "").strip()
        if source_commit:
            commits[task_id] = source_commit
    return commits


__all__ = ["candidate_task_source_commits", "same_task_map_generation"]
