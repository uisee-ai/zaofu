"""Keep old reader failures from competing with an active candidate replan."""

from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.reader_replan_index import ReaderReplanIndex


def admitted_reader_retrigger_owns_failure(
    stage: Any, failure: ZfEvent, index: ReaderReplanIndex,
) -> bool:
    """A kernel-admitted explicit successor owns recovery, never the verdict.

    Retain the failed generation and findings. Its successor's own failures are
    separate inputs; an unrelated busy Reader or a claimed redrive is no proof.
    """
    def run(event: ZfEvent) -> str:
        p = event.payload
        return str(p.get('workflow_run_id') or p.get('run_id') or p.get('trace_id')
                   or event.correlation_id or '')

    failure_position = index.positions.get(failure.id, -1)
    fanout = failure.payload.get('fanout_id')
    identity = run(failure)
    if failure_position < 0 or not fanout or not identity:
        return False
    starts = index.rows('fanout.started')
    origins = [index.by_id.get(e.payload.get('trigger_event_id')) for position, e in starts
        if position < failure_position and e.origin == 'kernel'
        and e.payload.get('fanout_id') == fanout and e.payload.get('stage_id') == stage.id
        and run(e) == identity]
    if len(origins) != 1 or origins[0] is None:
        return False
    original = origins[0]
    if original.type != stage.trigger or run(original) != identity:
        return False
    for position, admission in starts:
        if (position <= failure_position or admission.origin != 'kernel'
                or admission.payload.get('stage_id') != stage.id or run(admission) != identity):
            continue
        child = index.by_id.get(admission.payload.get('trigger_event_id'))
        if child is None or not failure_position < index.positions[child.id] < position:
            continue
        seen: set[str] = set()
        while child is not None and child.id not in seen:
            if (child.type != stage.trigger or run(child) != identity
                    or any(child.payload.get(k) != original.payload.get(k)
                           for k in ('pdd_id', 'feature_id', 'task_id', 'task_map_generation'))
                    or child.task_id != original.task_id):
                break
            seen.add(child.id)
            parent_id = child.payload.get('redrive_of')
            parent = index.by_id.get(parent_id)
            if (not parent_id or child.payload.get('rework_of') != parent_id or parent is None
                    or index.positions[parent.id] >= index.positions[child.id]):
                break
            if parent.id == original.id:
                return True
            child = parent
    return False


def admitted_candidate_plan_recovery_owns_failure(stage: Any, failure: ZfEvent) -> bool:
    """Keep an admitted Plan-port blocker on the candidate recovery route."""
    return getattr(stage, "trigger", "") == "candidate.ready" and admitted_plan_blocker(failure)


def admitted_plan_blocker(failure: ZfEvent) -> bool:
    payload = failure.payload
    refs = payload.get("semantic_result_refs")
    return (
        getattr(failure, "origin", "") == "kernel"
        and payload.get("failure_class") == "dependency_blocked"
        and payload.get("recovery_action") == "replan"
        and payload.get("rework_scope") == "plan_ports"
        and isinstance(refs, list)
        and any(str(ref or "").strip() for ref in refs)
    )


def active_candidate_replan_owns_failure(
    config: Any, events: list[ZfEvent], failure: ZfEvent, *,
    event_index: ReaderReplanIndex | None = None,
) -> bool:
    """Use explicit repair lineage, not merely another busy agent in the Run."""
    if event_index is not None:
        event_index.require_snapshot(events)
    plan_stages = {
        str(stage.id): str(stage.trigger)
        for stage in getattr(config.workflow, "stages", [])
        if getattr(stage, "attempt_domain", "") == "plan"
        and getattr(stage, "topology", "") == "fanout_reader"
    }
    payload = failure.payload
    if str(payload.get("stage_id") or "") in plan_stages:
        return False  # The new planner's own failures still consume its cap.

    def scope(event: ZfEvent) -> tuple[str, str, str]:
        body = event.payload
        return (
            str(body.get("workflow_run_id") or body.get("run_id") or event.correlation_id or ""),
            str(body.get("pdd_id") or body.get("feature_id") or ""),
            str(body.get("task_map_generation") or ""),
        )

    identity = scope(failure)
    if not all(identity):
        return False
    failure_index = (event_index.positions.get(failure.id, -1) if event_index is not None
                     else next((i for i, event in enumerate(events) if event.id == failure.id), -1))
    if failure_index < 0:
        return False
    history = ([event for position, event in event_index.ordered({
        "orchestrator.replan_requested", "fanout.started", "fanout.aggregate.completed",
        "fanout.cancelled", "fanout.timed_out",
    }) if position < failure_index] if event_index is not None else events[:failure_index])
    by_id = event_index.by_id if event_index is not None else {event.id: event for event in history}
    repairs: dict[tuple[str, str], ZfEvent] = {}
    active: set[str] = set()
    for event in history:
        body = event.payload
        fanout_id = str(body.get("fanout_id") or "")
        if event.type in {"fanout.aggregate.completed", "fanout.cancelled", "fanout.timed_out"}:
            active.discard(fanout_id)
            continue
        repair_key = (str(body.get("rework_of") or ""), str(body.get("rework_attempt") or ""))
        if event.type == "orchestrator.replan_requested":
            if scope(event) != identity:
                continue
            if body.get("rework_source") not in {
                "candidate.conflict", "candidate.quality.failed", "integration.failed",
            } or not repair_key[0]:
                continue
            affected = set(body.get("failed_task_ids") or body.get("task_ids") or [])
            failure_tasks = set(payload.get("affected_task_ids") or [])
            task_id = str(payload.get("task_id") or failure.task_id or "")
            if task_id:
                failure_tasks.add(task_id)
            if affected and (not failure_tasks or not failure_tasks <= affected):
                continue
            repairs[repair_key] = event
        elif event.type == "fanout.started" and fanout_id:
            # fanout.started carries Run/PDD; its canonical trigger carries
            # the task-map generation (the manifest only embeds that payload).
            event_scope = scope(event)
            if event_scope[:2] != identity[:2] or (
                event_scope[2] and event_scope[2] != identity[2]
            ):
                continue
            trigger_type = plan_stages.get(str(body.get("stage_id") or ""))
            trigger = by_id.get(str(body.get("trigger_event_id") or ""))
            if trigger is None or trigger.type != trigger_type or scope(trigger) != identity:
                continue
            if event_index is not None and event_index.positions[trigger.id] >= failure_index:
                continue  # The snapshot lookup must retain the historical prefix boundary.
            trigger_key = (str(trigger.payload.get("rework_of") or ""),
                           str(trigger.payload.get("rework_attempt") or ""))
            if trigger_key in repairs:
                active.add(fanout_id)
    return bool(active)
