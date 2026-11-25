"""Route current admitted Candidate findings to their existing semantic owner."""
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.orchestrator_agent_policy import checkpoint_policy, orchestration_flow_kind
from zf.runtime.run_contract import stable_json_sha256


def candidate_review_binding(state_dir: Path | None, config: Any,
                             events: list[ZfEvent], failure: ZfEvent) -> dict | None:
    p = failure.payload
    run_id = p.get("workflow_run_id")
    task_id = p.get("pdd_id")
    infra = p.get("failure_kind") == "infra"
    if (state_dir is None or failure.origin != "kernel"
            or failure.type not in {"test.failed", "verify.failed"}
            or not run_id or not task_id or not p.get("fanout_id")
            or p.get("failure_kind") == "budget"
            or (infra and p.get("failure_class") != "dependency_blocked")):
        return None
    package = next((e.payload for e in reversed(events) if e.type == "plan.artifact_package.admitted"
                    and (e.payload.get("workflow_run_id") or e.correlation_id) == run_id), {})
    flow = str(package.get("flow_kind") or orchestration_flow_kind(failure))
    if checkpoint_policy(config, "semantic_failure", flow_kind=flow) != "blocking":
        return None
    task = TaskStore(state_dir / "kanban.json").get(task_id)
    if task is None or task.status in {"done", "cancelled"}:
        return None
    if any(e.origin == "kernel" and e.type in {
        "run.goal.completed", "run.goal.failed", "run.aborted", "run.cancelled", "run.failed",
    } and (e.payload.get("workflow_run_id") or e.payload.get("run_id") or e.correlation_id) == run_id
            for e in events):
        return None
    latest = next((e for e in reversed(events) if e.origin == "kernel"
        and e.type in {"test.failed", "verify.failed"} and e.payload.get("fanout_id") == p["fanout_id"]
        ), None)
    native = next((e for e in reversed(events) if e.origin == "kernel" and e.type == "candidate.ready"
                   and (e.payload.get("workflow_run_id") or e.correlation_id) == run_id), None)
    start = next((e for e in events if e.origin == "kernel" and e.type == "fanout.started"
                  and e.payload.get("fanout_id") == p["fanout_id"]), None)
    aggregate = next((e for e in events if e.id == failure.causation_id), None)
    if (latest is None or latest.id != failure.id or native is None or start is None
            or start.payload.get("trigger_event_id") != native.id
            or native.payload.get("pdd_id") != task_id
            or native.payload.get("task_map_generation") != p.get("task_map_generation")
            or aggregate is None or aggregate.origin != "kernel"
            or aggregate.type != "fanout.aggregate.completed"
            or aggregate.payload.get("fanout_id") != p["fanout_id"] or aggregate.payload.get("status") != "failed"):
        return None
    try:
        # An infra-labeled dependency transfers to OA only when all failed Readers
        # submitted a completed, admitted dependency verdict for this frozen target.
        bound = aggregate_candidate_binding(state_dir, events, failure_id=failure.id,
            run_id=run_id, task_id=task_id, require_completed_dependencies=infra)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        if infra:
            return None  # Keep unproven execution failures on their existing recovery route.
        # Missing evidence does not transfer this current failure to generic retry.
        return {"workflow_run_id": run_id, "flow_kind": flow, "anchor_task_id": task_id,
                "pdd_id": task_id, "task_map_generation": p.get("task_map_generation"),
                "candidate_event_id": native.id, "binding_error": str(exc)}
    if (bound["task_map_generation"] != package.get("task_map_generation")
            or bound["plan_artifact_package_digest"] != package.get("package_digest")):
        return None
    return {**bound, "workflow_run_id": run_id, "flow_kind": flow}


def pending_candidate_reviews(*, state_dir: Path, config: Any, events: list[ZfEvent]) -> list[dict]:
    from zf.runtime.orchestrator_agent_execution_policy import run_goal_objective_snapshot

    actions = []
    stale_operations = {e.payload.get("operation_id") for e in events
        if e.origin == "kernel" and e.type == "orchestrator.semantic.decision.rejected"
        and e.payload.get("reason") == "execution_goal_changed"}
    stale_requests = {e.causation_id for e in events
        if e.origin == "kernel" and e.type == "orchestrator.semantic.checkpoint.requested"
        and e.payload.get("operation_id") in stale_operations}
    for failure in events:
        bound = candidate_review_binding(state_dir, config, events, failure)
        if bound is None:
            continue
        run_id = bound["workflow_run_id"]
        requests = [e for e in events if e.origin == "kernel" and e.type == "orchestrator.semantic.failure.requested"
               and e.id not in stale_requests
               and e.payload.get("workflow_run_id") == run_id
               and e.payload.get("recovery_scope") == "candidate"
               and failure.id in e.payload.get("failure_event_ids", [])]
        rejected = next((e for e in reversed(events)
            if e.origin == "kernel" and e.type == "orchestrator.semantic.checkpoint.rejected"
            and (e.payload.get("source_event_id") or e.causation_id) in {r.id for r in requests}), None)
        if requests and rejected is None and not bound.get("binding_error"):
            continue
        objective = run_goal_objective_snapshot(events, run_id)["objective_event_id"]
        request = "oa-candidate-review-" + stable_json_sha256({
            "failure": failure.id, "run": run_id, "generation": bound["task_map_generation"],
            "candidate": bound["candidate_event_id"], "objective": objective,
        })[:24]
        actions.append({
            "schema_version": "run-manager.pending-action.v1",
            "action": "orchestrator-rework-triage", "safe_resume_action": "request_orchestrator_triage",
            "checkpoint_id": request, "request_id": request, "fingerprint": request,
            "task_id": bound["anchor_task_id"], "pdd_id": bound["pdd_id"],
            "workflow_run_id": run_id, "flow_kind": bound["flow_kind"],
            "fanout_id": failure.payload["fanout_id"],
            "task_map_generation": bound["task_map_generation"], "run_goal_objective_event_id": objective,
            "source_event_id": failure.id, "source_event_type": failure.type,
            "source_event_ids": [failure.id], "failure_event_ids": [failure.id], "failure_count": 1,
            "failure_class": "candidate_aggregate_requires_semantic_review",
            "owner_route": "run_manager", "action_policy": "auto_decide",
            "intervention_class": "semantic_replan", "recovery_scope": "candidate",
            "summary": "Review every admitted Candidate outcome before selecting bounded recovery; preserve unknown results.",
            "expected_downstream_events": ["orchestrator.semantic.failure.requested"],
            "verify_condition": "expected_downstream_event:orchestrator.semantic.failure.requested",
        })
        if rejected is not None or bound.get("binding_error"):
            diagnostic = rejected or failure
            actions[-1].update({
                "action": "diagnose-attention", "safe_resume_action": "diagnose_attention",
                "checkpoint_id": "candidate-review-unavailable:" + diagnostic.id,
                "request_id": "candidate-review-unavailable:" + diagnostic.id, "fingerprint": diagnostic.id,
                "source_event_id": diagnostic.id, "source_event_ids": [diagnostic.id],
                "source_event_type": diagnostic.type, "failure_class": "unknown",
                "action_policy": "needs_diagnosis", "intervention_class": "diagnose",
                "suggested_route": "run_manager_resident_agent",
                "summary": "Candidate aggregate evidence/checkpoint unavailable; diagnose the binding before retrying.",
                "binding_error": bound.get("binding_error", ""),
                "expected_downstream_events": ["run.manager.resident.prompted"],
                "effect_expected_events": ["run.manager.agent.recommendation"],
                "continuation_expected_events": ["run.manager.agent.recommendation"],
                "expected_output": ["run.manager.agent.recommendation"],
                "verify_condition": "expected_downstream_event:run.manager.resident.prompted",
            })
    return actions


def is_candidate_review_request(payload: dict) -> bool:
    """Shape-only preflight; the executor revalidates the complete binding."""
    return (payload.get("failure_class") == "candidate_aggregate_requires_semantic_review"
        and payload.get("source_event_type") in {"test.failed", "verify.failed"}
        and payload.get("recovery_scope") == "candidate" and not payload.get("target_operation_id")
        and bool(payload.get("task_map_generation")) and int(payload.get("failure_count") or 0) == 1)
