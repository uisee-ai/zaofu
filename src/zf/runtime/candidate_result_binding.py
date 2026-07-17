"""Strictly bind task-level results to an integrated candidate."""

from __future__ import annotations

from collections.abc import Sequence

from zf.core.events.model import ZfEvent


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
    different candidate SHA with the same change. A task result is bound only
    when the current candidate explicitly lists that task and the latest
    same-run task ref recorded before the candidate points to its exact target.
    """

    candidate_index = -1
    completed_task_ids: set[str] = set()
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.type != "candidate.ready" or not isinstance(event.payload, dict):
            continue
        body = event.payload
        event_run = str(
            body.get("workflow_run_id")
            or body.get("trace_id")
            or event.correlation_id
            or ""
        )
        if workflow_run_id and event_run != workflow_run_id:
            continue
        if str(body.get("candidate_head_commit") or "") != candidate_head_commit:
            continue
        completed_task_ids = {
            str(item).strip()
            for item in body.get("completed_task_ids") or []
            if str(item).strip()
        }
        candidate_index = index
        break
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
        event_run = str(
            body.get("workflow_run_id")
            or body.get("trace_id")
            or event.correlation_id
            or ""
        )
        if workflow_run_id and event_run != workflow_run_id:
            continue
        source_commit = str(body.get("source_commit") or "").strip()
        if source_commit:
            commits[task_id] = source_commit
    return commits


__all__ = ["candidate_task_source_commits", "same_task_map_generation"]
