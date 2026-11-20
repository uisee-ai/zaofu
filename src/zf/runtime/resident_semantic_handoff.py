"""Bind resident advice to current Impl checkpoints or legacy advisory review."""
from pathlib import Path
from typing import Any

from zf.runtime.orchestrator_agent_execution_review import pending_execution_reviews
from zf.runtime.orchestrator_agent_execution_policy import (
    operation_bindings,
    run_goal_objective_snapshot,
)


def bind_resident_semantic_handoff(
    *, state_dir: Path, config: Any, events: list, payload: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "")
    if not request_id or not payload.get("recorded_event_id"):
        raise ValueError("semantic_handoff_requires_bound_diagnosis")
    request = next((e for e in reversed(events)
                    if e.type == "run.manager.action.effect.pending"
                    and e.payload.get("request_id") == request_id), None)
    if request is None:
        raise ValueError("semantic_handoff_diagnosis_missing")
    failure_ids = set(request.payload.get("failure_event_ids") or [])
    operation_ids = frozenset(str(e.payload["operation_id"]) for e in events
                              if e.id in failure_ids and e.payload.get("operation_id"))
    candidates = pending_execution_reviews(state_dir=state_dir, config=config, events=events,
                                          diagnosed_operation_ids=operation_ids)
    current = next((a for a in candidates
                    if a.get("task_id") == payload.get("task_id")
                    and a.get("target_operation_id") in operation_ids), None)
    if current is None:
        raise ValueError("semantic_handoff_failure_not_current_or_disabled")
    for key in ("target_operation_id", "workflow_run_id", "task_map_generation", "active_attempt_id"):
        if payload.get(key) and payload[key] != current.get(key):
            raise ValueError(f"semantic_handoff_binding_mismatch:{key}")
    run_id, task_id = current["workflow_run_id"], current["task_id"]
    goal = run_goal_objective_snapshot(events, run_id)["objective_event_id"]
    requested_goal = run_goal_objective_snapshot(
        events[:events.index(request) + 1], run_id,
    )["objective_event_id"]
    if goal != requested_goal:
        raise ValueError("semantic_handoff_goal_changed")
    precondition = {
        "scope": "task_operation", "run_id": run_id, "task_id": task_id,
        "operation": operation_bindings(events, run_id, {task_id})[task_id],
        "goal_objective_event_id": goal,
    }
    # Reuse the review's admission evidence and policy, but continue the same
    # diagnosis case. This request only asks the configured OA for a decision.
    return {
        **payload, **current,
        **{key: payload[key] for key in (
            "request_id", "checkpoint_id", "recorded_event_id", "recovery_case_id",
        ) if key in payload},
        "recommended_route": "controlled_action",
        "summary": str(payload.get("summary") or current["summary"]),
        "source_event_ids": [*current["source_event_ids"], payload["recorded_event_id"]],
        "operation_precondition": precondition,
    }


def semantic_handoff_precondition_holds(events: list, action: dict, precondition: dict) -> bool:
    """Other task progress cannot invalidate this exact settled failure review.

    The caller still enforces Run terminal/pause, human, policy and budget gates.
    A new attempt, generation, admitted result or goal invalidates this scope.
    """
    run_id, task_id = precondition.get("run_id"), precondition.get("task_id")
    expected = precondition.get("operation")
    if (action.get("action") != "orchestrator-rework-triage" or not run_id or not task_id
            or action.get("workflow_run_id") != run_id or action.get("task_id") != task_id
            or not isinstance(expected, dict) or expected.get("status") != "settled"
            or not expected.get("admitted_control_result_ref")
            or any(not expected.get(key) or expected[key] != action.get(action_key)
                   for key, action_key in (
                       ("operation_id", "target_operation_id"),
                       ("active_attempt_id", "active_attempt_id"),
                       ("task_map_generation", "task_map_generation")))
            or "goal_objective_event_id" not in precondition):
        return False
    return (operation_bindings(events, run_id, {task_id}).get(task_id) == expected
            and run_goal_objective_snapshot(events, run_id)["objective_event_id"]
            == precondition["goal_objective_event_id"])
