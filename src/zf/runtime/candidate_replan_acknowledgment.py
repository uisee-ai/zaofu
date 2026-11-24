"""Finish an interrupted OA replan from its kernel-published marker."""

from collections.abc import Mapping

from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.replan_resynth import append_replan_resynth_once, plan_missing_replan_resynth_events
from zf.runtime.run_admission import build_run_admission_projection


def published_candidate_replan(
    runtime: Any, event: ZfEvent, outcome: Mapping, context: Mapping, delta_ref: Mapping,
) -> dict:
    """Called only after current decision admission, before rejecting an already replanned failure."""
    if outcome.get("decision") != "return_to_plan" or context.get("candidate_failure_scope") != "aggregate":
        return {}
    events = runtime.event_log.read_all()
    run = outcome["workflow_run_id"]
    ref = outcome["decision_ref"]
    markers = [e for e in events if e.type == "orchestrator.replan_requested" and e.origin == "kernel"
               and e.actor == "zf-cli" and e.causation_id == event.id and e.correlation_id == run
               and e.payload.get("workflow_run_id") == run
               and e.payload.get("orchestrator_operation_id") == outcome["operation_id"]
               and e.payload.get("orchestrator_decision_ref") == ref
               and e.payload.get("orchestration_delta_digest") == delta_ref["sha256"]
               and e.payload.get("candidate_failure_id") == context["candidate_failure_id"]
               and e.payload.get("rework_summary", {}).get("candidate_obligations")]
    if len(markers) != 1:
        return {}
    marker = markers[0]
    entry = build_run_admission_projection(events).runs.get(run)
    if entry and entry.status == "running":
        for trigger in plan_missing_replan_resynth_events(events, config=runtime.config, state_dir=runtime.state_dir):
            if trigger.correlation_id == run and trigger.payload.get("orchestrator_decision_ref") == ref:
                append_replan_resynth_once(runtime.event_writer, runtime.state_dir, trigger)
    return {**outcome, "semantic_replan_event_ids": [marker.id], "publication_replayed": True}
