"""Bind resident advice to the existing current Impl semantic-review route."""
from pathlib import Path
from typing import Any

from zf.runtime.orchestrator_agent_execution_review import pending_execution_reviews


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
    operation_ids = {e.payload.get("operation_id") for e in events if e.id in failure_ids}
    candidates = pending_execution_reviews(state_dir=state_dir, config=config, events=events)
    current = next((a for a in candidates
                    if a.get("task_id") == payload.get("task_id")
                    and a.get("target_operation_id") in operation_ids), None)
    if current is None:
        raise ValueError("semantic_handoff_failure_not_current_or_disabled")
    for key in ("target_operation_id", "workflow_run_id", "task_map_generation", "active_attempt_id"):
        if payload.get(key) and payload[key] != current.get(key):
            raise ValueError(f"semantic_handoff_binding_mismatch:{key}")
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
    }
