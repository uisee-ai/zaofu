"""Resolve provider hook assignments from current kernel dispatch facts."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations


def active_task_pipeline_dispatch(events: list[ZfEvent], actor: str) -> ZfEvent | None:
    candidates = [
        row
        for row in reduce_workflow_operations(events).values()
        if row.get("status") == "running"
        and row.get("role_instance") == actor
        and row.get("task_pipeline_stage")
    ]
    if len(candidates) != 1:
        return None
    current = candidates[0]
    for event in reversed(events):
        if event.type != "task.pipeline.stage.dispatched":
            continue
        payload = event.payload
        if (
            payload.get("operation_id") == current["operation_id"]
            and payload.get("role_instance") == actor
            and str(event.task_id or payload.get("task_id") or "") == current["task_id"]
            and all(
                payload.get(key) == current.get(key)
                for key in (
                    "dispatch_id",
                    "task_map_generation",
                    "workspace_generation",
                    "placement_epoch",
                )
            )
        ):
            return event
    return None


def _fanout_key(payload: dict) -> tuple[str, str, str]:
    return (
        str(payload.get("fanout_id") or ""),
        str(payload.get("child_id") or payload.get("child_run") or ""),
        str(payload.get("run_id") or ""),
    )


def _fanout_terminal_keys(payload: dict) -> set[tuple[str, str, str]]:
    fanout_id, child_id, run_id = _fanout_key(payload)
    if not fanout_id or not child_id:
        return set()
    keys = {(fanout_id, child_id, "")}
    if run_id:
        keys.add((fanout_id, child_id, run_id))
    return keys


def _latest_active_dispatch_for_actor(
    events: list[ZfEvent],
    actor: str,
) -> str | None:
    pipeline = active_task_pipeline_dispatch(events, actor)
    if pipeline is not None:
        return pipeline.id
    terminal_fanout_children: set[tuple[str, str, str]] = set()
    terminal_synth_fanouts: set[str] = set()
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {"fanout.child.completed", "fanout.child.failed"}:
            terminal_fanout_children.update(_fanout_terminal_keys(payload))
            continue
        if event.type in {"fanout.synth.completed", "fanout.aggregate.completed"}:
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                terminal_synth_fanouts.add(fanout_id)
            continue
        if event.type == "fanout.timed_out":
            fanout_id = str(payload.get("fanout_id") or "")
            for child_id in payload.get("pending_children", []) or []:
                child_id = str(child_id or "")
                if fanout_id and child_id:
                    terminal_fanout_children.add((fanout_id, child_id, ""))
            continue
        if event.type == "fanout.child.dispatched":
            if str(payload.get("role_instance") or "") != actor:
                continue
            key = _fanout_key(payload)
            child_done = key in terminal_fanout_children
            child_done = child_done or (
                (key[0], key[1], "") in terminal_fanout_children
            )
            if not child_done:
                return event.id
            continue
        if event.type == "fanout.synth.dispatched":
            # B3 (R25 ISSUE-005): a dispatched synth is an active context
            # for its role's hooks until the synth/aggregate completes.
            if str(payload.get("role_instance") or "") != actor:
                continue
            if str(payload.get("fanout_id") or "") not in terminal_synth_fanouts:
                return event.id
            continue
        if event.type != "task.dispatched":
            continue
        if payload.get("assignee") == actor or payload.get("role") == actor:
            return event.id
    return None
