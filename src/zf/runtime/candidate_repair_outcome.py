"""Recover acknowledgment of an already published, admitted OA repair."""
from dataclasses import replace
from pathlib import Path
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
    publication = _published_repair(runtime.state_dir, events, decision_event.id)
    if not publication:
        return {}
    admitted, _, amendment, ready = publication
    return {**admitted.payload, "status": "applied", "applied": True,
            "candidate_repair_event_ids": [amendment.id, ready.id], "publication_replayed": True}


def _published_repair(
    state: Path, events: list[ZfEvent], decision_id: str,
) -> tuple[ZfEvent, dict, ZfEvent, ZfEvent] | None:
    admitted = next((e for e in events if e.type == "orchestrator.semantic.decision.admitted"
        and e.origin == "kernel" and e.causation_id == decision_id
        and e.payload.get("status") == "admitted"
        and e.payload.get("checkpoint") == "semantic_failure" and e.payload.get("decision") == "rework"
        and e.payload.get("checkpoint_policy") != "shadow"), None)
    if admitted is None:
        return None
    run_id = admitted.payload.get("workflow_run_id")
    checkpoint = "oa-candidate-repair-" + admitted.payload["operation_id"]
    starts = [e for e in events[events.index(admitted) + 1:]
        if e.type == "runtime.action.attempt.started" and e.origin == "kernel"
        and e.causation_id == decision_id and e.payload.get("action") == "candidate-rework-apply"]
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
            hydrate_sidecar_ref(state, {"ref": body["task_map_ref"], "sha256": body["task_map_digest"]})
        except (OSError, ValueError, KeyError):
            continue
        # This acknowledges the historical effect; it does not re-admit the
        # obsolete Candidate or execute any action against the current Run.
        return admitted, action, amendment, ready
    return None


def repair_flow_kind(state: Path, action: dict) -> str:
    """Use the predecessor package pinned in the controlled repair action."""
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package

    package = hydrate_plan_artifact_package(state, {
        "ref": action["plan_artifact_package_ref"], "sha256": action["plan_artifact_package_digest"],
    }, validate_ports=False)
    task_map = next((port for port in [*package["produced"], *package["inherited"]]
                     if port["logical_name"] == "task_map"), {})
    if (package["workflow_run_id"] != action.get("workflow_run_id")
            or package["task_map_generation"] != action.get("task_map_generation")
            or task_map.get("ref") != action.get("task_map_ref")):
        raise ValueError("Candidate repair does not match its predecessor Plan")
    return package["flow_kind"]


def routable_candidate_repair(runtime: Any, event: ZfEvent) -> ZfEvent | None:
    """Route an OA repair only with its native publication and bound predecessor."""
    if event.type != "task_map.ready" or not isinstance(event.payload, dict):
        return event
    body = event.payload
    checkpoint = body.get("resume_checkpoint_ref")
    if (not isinstance(checkpoint, str)
            or not checkpoint.startswith("oa-candidate-repair-")):
        return event
    if event.origin != "kernel" or body.get("resume_scope") != "gap_tasks_only":
        return None
    from zf.runtime.candidate_rework_generation import current_rework_task_map_replay_candidates

    events = runtime.event_log.read_all()
    run = body.get("workflow_run_id")
    run_events = [e for e in events if (e.payload.get("workflow_run_id") or e.correlation_id) == run
                  and e.origin not in {"worker", "external"}]
    if not any(candidate.id == event.id for candidate in current_rework_task_map_replay_candidates(run_events)):
        return None
    decision_id = next((e.causation_id for e in events
                        if e.type == "orchestrator.semantic.decision.admitted" and e.origin == "kernel"
                        and e.payload.get("operation_id") == checkpoint.removeprefix("oa-candidate-repair-")
                        and e.payload.get("workflow_run_id") == body.get("workflow_run_id")), None)
    publication = _published_repair(runtime.state_dir, events, decision_id) if decision_id else None
    if not publication:
        return None
    _, action, amended, ready = publication
    if (event.id != ready.id or ready.payload != body or event.causation_id != ready.causation_id
            or event.actor != ready.actor or event.correlation_id != ready.correlation_id):
        return None
    try:
        kind = repair_flow_kind(runtime.state_dir, action)
    except (KeyError, OSError, ValueError):
        return None
    if any(row.payload.get("flow_kind") not in (None, kind) for row in (amended, ready)):
        return None
    return event if body.get("flow_kind") else replace(event, payload={**body, "flow_kind": kind})
