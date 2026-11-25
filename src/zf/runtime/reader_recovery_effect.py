"""Bind a dispatched Candidate Reader replacement to the triage effect it displaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from zf.core.events.model import ZfEvent


def reader_triage_scope(body: Mapping) -> tuple[str, ...]:
    if body.get("action") != "orchestrator-rework-triage":
        return ()
    scope = tuple(body.get(k, "") for k in (
        "source_event_id", "workflow_run_id", "task_map_generation", "fanout_id",
    ))
    return scope if all(isinstance(v, str) and v for v in scope) else ()


def dispatched_reader_replacements(events: Sequence[ZfEvent]) -> set[tuple[str, ...]]:
    """Require the same frozen target, applied controlled action and actual new dispatch."""
    by_id = {e.id: e for e in events}
    # Dispatch can race publication of the control service's completion receipt.
    applied = {e.payload.get("result_event_id") for e in events
               if e.origin == "kernel" and e.type == "runtime.action.attempt.completed"
               and e.payload.get("action") == "stage-replan-new-generation"
               and e.payload.get("status") == "applied" and e.payload.get("result_event_id")}
    replaced = set()
    for child in events:
        if child.origin != "kernel" or child.type != "fanout.child.dispatched" or child.causation_id not in applied:
            continue
        trigger = by_id.get(child.causation_id)
        if not trigger or trigger.type != "candidate.ready" or trigger.origin != "kernel":
            continue
        body, dispatch = trigger.payload, child.payload
        failure = by_id.get(body.get("repaired_failure_event_id"))
        frozen = by_id.get(body.get("candidate_snapshot_event_id"))
        if (not failure or failure.type != "test.failed" or failure.origin != "kernel"
                or not frozen or frozen.type != "candidate.ready" or frozen.origin != "kernel"):
            continue
        source, snapshot = failure.payload, frozen.payload
        if not (body.get("operator_authorized") is True and trigger.causation_id
                and body.get("stage_replan_generation") == f"repair-{trigger.causation_id}"
                and source.get("candidate_event_id") == frozen.id and snapshot.get("status") == "frozen"
                and body.get("stage_replan_generation_source_event_id") == failure.id
                and dispatch.get("stage_replan_generation_source_event_id") == failure.id
                and dispatch.get("stage_replan_generation") == body["stage_replan_generation"]):
            continue
        run, generation = body.get("workflow_run_id"), body.get("task_map_generation")
        target = snapshot.get("candidate_head_commit")
        if (not run or not generation or any(
                (row.get("workflow_run_id"), row.get("task_map_generation")) != (run, generation)
                for row in (dispatch, source, snapshot))):
            continue
        if (target and body.get("candidate_head_commit") == dispatch.get("target_commit") == target
                and source.get("stage_id") and dispatch.get("stage_id") == source["stage_id"]
                and source.get("fanout_id") and dispatch.get("fanout_id")
                and dispatch["fanout_id"] != source["fanout_id"]):
            replaced.add((failure.id, run, generation, source["fanout_id"]))
    return replaced
