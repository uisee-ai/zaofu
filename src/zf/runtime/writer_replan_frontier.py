"""Retire queued writer work superseded by a completed Owner Plan recovery."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id, run_aliases
from zf.runtime.stage_replan_generation import completed_generation_source


def _pdd(event: ZfEvent) -> str:
    body = event.payload
    return str(body.get("pdd_id") or body.get("feature_id")
               or body.get("parent_task_id") or event.task_id or "")


def retired_writer_trigger(
    events: Sequence[ZfEvent], trigger: ZfEvent, config: Any,
) -> ZfEvent | None:
    """Return the bound Plan trigger that superseded this earlier writer intent."""
    if trigger.type != "task_map.ready":
        return None
    aliases = run_aliases(events)
    run_id = event_run_id(trigger, aliases=aliases)
    pdd_id = _pdd(trigger)
    if not run_id or not pdd_id:
        return None
    stages = {stage.id: stage for stage in config.workflow.stages}
    prior: dict[str, ZfEvent] = {}
    positions: dict[str, int] = {}
    for index, event in enumerate(events):
        source = completed_generation_source(event, prior, aliases)
        if source and event.origin not in {"worker", "external"}:
            body = event.payload
            newer = prior[str(body["stage_trigger_event_id"])]
            goal = prior[str(body["run_goal_update_event_id"])]
            failure = prior.get(str(source.payload.get("source_event_id") or ""))
            stage = stages.get(str(failure.payload.get("stage_id") or "")) if failure else None
            if (
                stage is not None and stage.attempt_domain == "plan"
                and newer.type == stage.trigger
                and trigger.id in positions
                and positions[trigger.id] < positions[newer.id]
                and newer.origin not in {"worker", "external"}
                and goal.origin not in {"worker", "external"}
                and event_run_id(newer, aliases=aliases) == run_id
                and _pdd(newer) == pdd_id
            ):
                return newer
        prior[event.id] = event
        positions[event.id] = index
    return None


def suppress_retired_writer_trigger(runtime: Any, trigger: ZfEvent) -> bool:
    events = runtime.event_log.read_all()
    newer = retired_writer_trigger(events, trigger, runtime.config)
    if newer is None:
        return False
    reason = "writer_trigger_retired_by_authorized_plan_generation"
    if not any(event.type == "fanout.retrigger.suppressed"
               and event.payload.get("trigger_event_id") == trigger.id
               and event.payload.get("reason") == reason for event in events):
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed", actor="zf-cli", origin="kernel",
            payload={"trigger_event_id": trigger.id, "reason": reason,
                     "superseded_by": newer.id,
                     "workflow_run_id": event_run_id(newer, aliases=run_aliases(events)),
                     "pdd_id": _pdd(newer),
                     "stage_replan_generation": newer.payload["stage_replan_generation"]},
            causation_id=trigger.id, correlation_id=newer.correlation_id,
        ))
    return True


def retired_plan_branch(
    events: Sequence[ZfEvent], trigger: ZfEvent, config: Any,
) -> ZfEvent | None:
    """Follow native causation to a writer intent retired by Owner recovery."""
    if not any(stage.topology == "fanout_reader" and stage.attempt_domain == "plan"
               and stage.trigger == trigger.type for stage in config.workflow.stages):
        return None
    by_id = {event.id: event for event in events}
    aliases = run_aliases(events)
    run_id, pdd_id = event_run_id(trigger, aliases=aliases), _pdd(trigger)
    current, visited = trigger, set()
    while current.id not in visited:
        visited.add(current.id)
        if (current.origin in {"worker", "external"}
                or event_run_id(current, aliases=aliases) != run_id
                or _pdd(current) != pdd_id):
            return None
        newer = retired_writer_trigger(events, current, config)
        if newer is not None:
            return newer
        parent = by_id.get(str(current.causation_id or ""))
        if parent is None:
            return None
        current = parent
    return None


def suppress_retired_plan_branch(runtime: Any, trigger: ZfEvent) -> bool:
    events = runtime.event_log.read_all()
    newer = retired_plan_branch(events, trigger, runtime.config)
    if newer is None:
        return False
    reason = "plan_branch_descends_from_retired_writer_trigger"
    if not any(event.type == "fanout.retrigger.suppressed"
               and event.payload.get("trigger_event_id") == trigger.id
               and event.payload.get("reason") == reason for event in events):
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed", actor="zf-cli", origin="kernel",
            payload={"trigger_event_id": trigger.id, "reason": reason,
                     "superseded_by": newer.id, "pdd_id": _pdd(newer)},
            causation_id=trigger.id, correlation_id=newer.correlation_id,
        ))
    return True
