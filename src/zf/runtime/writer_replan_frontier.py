"""Retire queued writer work superseded by a completed Owner Plan recovery."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id, run_aliases
from zf.runtime.stage_replan_generation import completed_generation_source, exhausted_failure_escalation
from zf.runtime.reader_replan_index import ReaderReplanIndex
from zf.runtime.fanout_retrigger_guard import stall_trigger_original


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
    original = stall_trigger_original(events, trigger) or trigger
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
                and original.id in positions
                and positions[original.id] < positions[newer.id]
                and newer.origin not in {"worker", "external"}
                and goal.origin not in {"worker", "external"}
                and event_run_id(newer, aliases=aliases) == run_id
                and _pdd(newer) == pdd_id
            ):
                return newer
        prior[event.id] = event
        positions[event.id] = index
    return None


def suppress_retired_writer_trigger(
    runtime: Any, trigger: ZfEvent, *, stage_ids: Sequence[str],
) -> bool:
    events = runtime.event_log.read_all()
    newer = retired_writer_trigger(events, trigger, runtime.config)
    if newer is None:
        return False
    reason = "writer_trigger_retired_by_authorized_plan_generation"
    original = stall_trigger_original(events, trigger) or trigger
    # Settle the root intent as well as the late copy, so stall detection does
    # not mistake retirement for another unhandled dispatch or exhaust retries.
    for target in {trigger.id: trigger, original.id: original}.values():
        for stage_id in sorted(set(stage_ids)):
            if any(event.type == "fanout.retrigger.suppressed"
                   and event.payload.get("trigger_event_id") == target.id
                   and event.payload.get("stage_id") == stage_id
                   and event.payload.get("reason") == reason for event in events):
                continue
            runtime.event_writer.append(ZfEvent(
                type="fanout.retrigger.suppressed", actor="zf-cli", origin="kernel",
                payload={"stage_id": stage_id, "trigger_event_id": target.id, "reason": reason,
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
    index = ReaderReplanIndex(events)
    run_id, pdd_id = index.run_id(trigger), _pdd(trigger)
    current, visited = trigger, set()
    while current.id not in visited:
        visited.add(current.id)
        if (current.origin in {"worker", "external"}
                or index.run_id(current) != run_id or _pdd(current) != pdd_id):
            return None
        parent = index.by_id.get(str(current.causation_id or ""))
        if current.actor == "zf-cli" and current.payload.get("rework_of") and parent:
            # A mechanical retry cites its failure. A controlled owner trigger cites its
            # request and may legitimately refer back to the same rejected candidate.
            cap = exhausted_failure_escalation(index, parent)
            if (cap and index.run_id(parent) == run_id
                    and index.positions[cap.id] < index.positions.get(current.id, -1)):
                return cap
        newer = retired_writer_trigger(events, current, config)
        if newer is not None:
            return newer
        if parent is None:
            return None
        current = parent
    return None


def plan_branch_retirement_reason(source: ZfEvent) -> str:
    if source.type == "human.escalate":
        return "automatic_plan_retry_after_owner_handoff"
    return "plan_branch_descends_from_retired_writer_trigger"


def suppress_retired_plan_branch(
    runtime: Any, trigger: ZfEvent, *, stage_ids: Sequence[str],
) -> bool:
    if not stage_ids:
        return False
    events = runtime.event_log.read_all()
    newer = retired_plan_branch(events, trigger, runtime.config)
    if newer is None:
        return False
    reason = plan_branch_retirement_reason(newer)
    for stage_id in sorted(set(stage_ids)):
        if any(event.type == "fanout.retrigger.suppressed"
               and event.payload.get("trigger_event_id") == trigger.id
               and event.payload.get("stage_id") == stage_id
               and event.payload.get("reason") == reason for event in events):
            continue
        runtime.event_writer.append(ZfEvent(
            type="fanout.retrigger.suppressed", actor="zf-cli", origin="kernel",
            payload={"stage_id": stage_id, "trigger_event_id": trigger.id, "reason": reason,
                     "superseded_by": newer.id, "pdd_id": _pdd(newer)},
            causation_id=trigger.id, correlation_id=newer.correlation_id,
        ))
    return True
