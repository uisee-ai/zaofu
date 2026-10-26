"""Keep old reader failures from competing with an active candidate replan."""

from typing import Any

from zf.core.events import ZfEvent


def active_candidate_replan_owns_failure(
    config: Any, events: list[ZfEvent], failure: ZfEvent,
) -> bool:
    """Use explicit repair lineage, not merely another busy agent in the Run."""
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
    failure_index = next((i for i, event in enumerate(events) if event.id == failure.id), -1)
    if failure_index < 0:
        return False
    history = events[:failure_index]
    by_id = {event.id: event for event in history}
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
            trigger_key = (str(trigger.payload.get("rework_of") or ""),
                           str(trigger.payload.get("rework_attempt") or ""))
            if trigger_key in repairs:
                active.add(fanout_id)
    return bool(active)
