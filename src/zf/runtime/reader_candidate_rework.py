"""Fence candidate readers after a controlled rework accepts that exact candidate."""

from __future__ import annotations

from collections.abc import Sequence

from zf.core.events.model import ZfEvent


def candidate_rework_reader_fences(
    events: Sequence[ZfEvent],
) -> dict[str, tuple[str, str]]:
    """Project exact Run/candidate invalidation without changing canonical state."""

    by_id: dict[str, ZfEvent] = {}
    reworks: dict[tuple[str, str], str] = {}
    readers: dict[str, tuple[str, str]] = {}
    for event in events:
        payload = event.payload or {}
        run_id = str(payload.get("workflow_run_id") or payload.get("trace_id")
                     or event.correlation_id or "")
        if event.type == "task_map.ready" and event.origin == "kernel":
            source = by_id.get(str(payload.get("rework_of") or ""))
            head = str(payload.get("candidate_head_commit") or "")
            if (
                run_id and head and source is not None
                and payload.get("source") in {
                    "run_manager_candidate_rework", "run_manager_gap_task_map_amend",
                }
                and payload.get("resume_checkpoint_ref")
                and payload.get("rework_source") == source.type
                and str(source.payload.get("workflow_run_id")
                        or source.payload.get("trace_id")
                        or source.correlation_id or "") == run_id
                and source.payload.get("candidate_head_commit") == head
            ):
                reworks[(run_id, head)] = event.id
        if event.type == "fanout.started" and payload.get("topology") == "fanout_reader":
            trigger = payload.get("trigger_payload") or {}
            head = str(trigger.get("candidate_head_commit")
                       or payload.get("candidate_head_commit") or "")
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id and run_id and head:
                readers[fanout_id] = (run_id, head)
        by_id[event.id] = event
    return {
        fanout_id: ("candidate_superseded_by_controlled_rework", reworks[key])
        for fanout_id, key in readers.items() if key in reworks
    }
