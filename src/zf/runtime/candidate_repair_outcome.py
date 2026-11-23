"""Recover acknowledgment of an already published, admitted OA repair."""
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def published_candidate_repair_outcome(runtime: Any, decision_event: ZfEvent) -> dict:
    if decision_event.type != "orchestrator.semantic.decision.submitted":
        return {}
    events = runtime.event_log.read_all()
    original = next((e for e in events if e.id == decision_event.id), None)
    if original is None or original.type != "orchestrator.semantic.decision.submitted":
        return {}
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event

    try:
        original = hydrate_profiled_control_result_event(runtime.state_dir, original)
    except (OSError, ValueError, KeyError):
        return {}
    if original.payload != decision_event.payload:
        return {}
    admitted = next((e for e in events if e.type == "orchestrator.semantic.decision.admitted"
        and e.origin == "kernel" and e.causation_id == decision_event.id
        and e.payload.get("status") == "admitted"
        and e.payload.get("checkpoint") == "semantic_failure" and e.payload.get("decision") == "rework"
        and e.payload.get("checkpoint_policy") != "shadow"), None)
    if admitted is None:
        return {}
    run_id = admitted.payload.get("workflow_run_id")
    checkpoint = "oa-candidate-repair-" + admitted.payload["operation_id"]
    starts = [e for e in events[events.index(admitted) + 1:]
        if e.type == "runtime.action.attempt.started" and e.origin == "kernel"
        and e.causation_id == decision_event.id and e.payload.get("action") == "candidate-rework-apply"]
    for start in starts:
        action = start.payload.get("payload") or {}
        if (action.get("checkpoint_id") != checkpoint or action.get("workflow_run_id") != run_id
                or action.get("candidate_rework_action") != "retrigger"
                or action.get("rework_summary", {}).get("preserve_task_rows") is not True):
            continue
        published = [e for e in events[events.index(start) + 1:]
            if e.origin == "kernel" and e.actor == "zf-cli" and e.correlation_id == run_id
            and e.causation_id == action.get("source_event_id")
            and e.payload.get("resume_checkpoint_ref") == checkpoint]
        amendments = [e for e in published if e.type == "task_map.amended"]
        readies = [e for e in published if e.type == "task_map.ready"]
        if len(amendments) != 1 or len(readies) != 1:
            continue
        amendment, ready = amendments[0], readies[0]
        if events.index(amendment) >= events.index(ready):
            continue
        body, amended = ready.payload, amendment.payload
        if any(body.get(key) != action.get(key) for key in (
            "workflow_run_id", "pdd_id", "source_commit", "candidate_base_commit", "candidate_ref",
            "rework_attempt", "rework_summary",
        )) or body.get("rework_of") != action.get("source_event_id"):
            continue
        if (body.get("amend_of") != action.get("task_map_ref")
                or body.get("supersedes_task_map_generation") != action.get("task_map_generation")
                or amended.get("supersedes_task_map_ref") != action.get("task_map_ref")
                or body.get("task_ids") != [row["task_id"] for row in action["rework_summary"]["gap_tasks"]]
                or any(body.get(key) != amended.get(key) for key in (
                    "workflow_run_id", "pdd_id", "task_map_ref", "task_map_digest", "task_map_generation",
                    "supersedes_task_map_generation", "gap_task_ids",
                ))):
            continue
        try:
            hydrate_sidecar_ref(runtime.state_dir, {"ref": body["task_map_ref"], "sha256": body["task_map_digest"]})
        except (OSError, ValueError, KeyError):
            continue
        # This acknowledges the historical effect; it does not re-admit the
        # obsolete Candidate or execute any action against the current Run.
        return {**admitted.payload, "status": "applied", "applied": True,
                "candidate_repair_event_ids": [amendment.id, ready.id], "publication_replayed": True}
    return {}
