"""Generation-scoped helpers for candidate recovery projection."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


def reset_generation_caches(
    event: ZfEvent,
    payload: dict,
    *,
    boundary_event_types: set[str] | frozenset[str],
    feedback_by_trace: dict[str, list[str]],
    failed_task_ids_by_trace: dict[str, set[str]],
    gap_tasks_by_trace: dict[str, list[dict[str, Any]]],
    rework_paths_by_trace: dict[str, list[str]],
) -> None:
    if event.type not in boundary_event_types:
        return
    trace_id = str(payload.get("trace_id") or event.correlation_id or "")
    if not trace_id:
        return
    feedback_by_trace.pop(trace_id, None)
    failed_task_ids_by_trace.pop(trace_id, None)
    gap_tasks_by_trace.pop(trace_id, None)
    rework_paths_by_trace.pop(trace_id, None)


def task_ids_from_payload(payload: dict) -> set[str]:
    task_ids: set[str] = set()
    explicit_failed_task_ids = payload.get("failed_task_ids")
    if isinstance(explicit_failed_task_ids, list):
        # An explicit failure set is authoritative, including ``[]``. Candidate
        # ``task_ids`` names integration inputs and must not silently reopen all
        # of them when the failure is candidate-scoped but unattributed.
        for item in explicit_failed_task_ids:
            if isinstance(item, str) and item.strip():
                task_ids.add(item.strip())
            elif isinstance(item, dict):
                value = str(item.get("task_id") or item.get("id") or "").strip()
                if value:
                    task_ids.add(value)
        return task_ids
    task_id = payload.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        task_ids.add(task_id.strip())
    # Completed slices are candidate inputs, not failed-task attribution.
    # Reopening them after a candidate-only failure breaks writer admission.
    for key in ("task_ids",):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str) and item.strip():
                task_ids.add(item.strip())
            elif isinstance(item, dict):
                value = str(item.get("task_id") or item.get("id") or "").strip()
                if value:
                    task_ids.add(value)
    findings = payload.get("findings")
    if not isinstance(findings, list):
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        findings = report.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            value = str(item.get("task_id") or item.get("task") or "").strip()
            if value:
                task_ids.add(value)
    return task_ids


def is_unattributed_candidate_worktree_drift(
    *,
    source_event_type: str,
    payload: dict[str, Any],
    failed_task_ids: tuple[str, ...],
) -> bool:
    if source_event_type != "integration.failed" or failed_task_ids:
        return False
    gates = payload.get("quality_gates_failed")
    return (
        str(payload.get("failure_class") or "")
        == "candidate_product_quality_failed"
        and (
            str(payload.get("diagnostic_class") or "")
            == "candidate_worktree_dirty"
            or isinstance(gates, list)
            and "candidate_worktree_clean" in gates
        )
    )


def current_rework_task_map_replay_candidates(
    events: list[ZfEvent],
) -> list[ZfEvent]:
    """Return only the latest non-superseded rework Task Map per run scope.

    Recovery may observe many duplicate ``task_map.ready`` events after an
    earlier stale-map retry loop. Replaying every historical duplicate makes
    startup catch-up quadratic because each admission reduces the full event
    window again. A workflow/PDD has one current Task Map generation, so only
    its latest non-superseded ready event is eligible for redrive.
    """

    superseded_generations: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        generation = str(
            payload.get("supersedes_task_map_generation") or ""
        ).strip()
        if generation:
            superseded_generations.add(generation)

    latest_by_scope: dict[tuple[str, str], tuple[int, ZfEvent]] = {}
    unscoped: list[tuple[int, ZfEvent]] = []
    for index, event in enumerate(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "run.goal.updated"
            and getattr(event, "origin", "") == "kernel"
            and payload.get("status") == "active"
            and payload.get("reason") == "authorized_stage_replan_new_generation"
            and payload.get("stage_replan_generation")
        ):
            run_id = str(payload.get("workflow_run_id") or payload.get("run_id")
                         or event.correlation_id or "").strip()
            if run_id:
                latest_by_scope = {scope: row for scope, row in latest_by_scope.items()
                                   if scope[0] != run_id}
        if event.type != "task_map.ready":
            continue
        generation = str(payload.get("task_map_generation") or "").strip()
        if generation and generation in superseded_generations:
            continue
        workflow_run_id = str(
            payload.get("workflow_run_id")
            or event.correlation_id
            or ""
        ).strip()
        pdd_id = str(
            payload.get("pdd_id")
            or payload.get("feature_id")
            or event.task_id
            or ""
        ).strip()
        if not workflow_run_id and not pdd_id:
            unscoped.append((index, event))
            continue
        latest_by_scope[(workflow_run_id, pdd_id)] = (index, event)

    selected = [*latest_by_scope.values(), *unscoped]
    # A newer ordinary Task Map also retires an older rework map, but only
    # rework maps belong to this recovery sweep. Do not resurrect old plans
    # merely because their replacement did not carry a rework_of marker.
    return [event for _index, event in sorted(selected, key=lambda item: item[0])
            if event.payload.get("rework_of")]


__all__ = [
    "current_rework_task_map_replay_candidates",
    "is_unattributed_candidate_worktree_drift",
    "reset_generation_caches",
    "task_ids_from_payload",
]
