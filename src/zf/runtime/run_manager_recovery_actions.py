"""Resident recommendations for existing deterministic recovery verbs."""
from typing import Any

RESIDENT_RECOVERY_ACTIONS = frozenset({
    "task-requeue", "child-rebuild", "stage-retrigger",
    "stage-replan-new-generation", "rescan-grant",
})
RECOVERY_FIELDS = (
    "task_id", "fanout_id", "fanout_child_id", "workflow_run_id",
    "target_operation_id", "active_attempt_id", "source_event_id",
    "reason", "approval_ref", "escalation_event_id",
    "supplemental_input_refs",
)


def resident_decision_question(payload: dict[str, Any]) -> str:
    disposition = payload.get("recommended_replan_or_resume_action")
    nested = disposition.get("decision_required") if isinstance(disposition, dict) else None
    action = payload.get("recommended_run_manager_action")
    action_question = action.get("question") if isinstance(action, dict) else None
    for value in (payload.get("question"), nested, action_question, payload.get("summary"), payload.get("reason")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def review_only_escalation(payload: dict[str, Any]) -> bool:
    return (payload.get("action") == "agent-recommendation"
            and payload.get("safe_resume_action") == "agent_recommendation")


def normalize_recovery_recommendation(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("controlled_action") or payload.get("action")
    if isinstance(name, dict):
        name = name.get("action") or name.get("kind")
    if not isinstance(name, str) or name not in RESIDENT_RECOVERY_ACTIONS:
        return payload
    nested = payload.get("action_payload", {})
    if not isinstance(nested, dict):
        raise ValueError("recovery action_payload must be an object")
    result = dict(payload)
    for key in RECOVERY_FIELDS:
        if key not in nested:
            continue
        if key in result and result[key] != nested[key]:
            raise ValueError(f"recovery action identity conflict: {key}")
        result[key] = nested[key]
    return result


def recovery_preflight(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    required = ("checkpoint_id",)
    if action == "child-rebuild":
        required += ("fanout_id", "fanout_child_id", "workflow_run_id",
                     "active_attempt_id", "source_event_id")
    return {"schema_version": "run-manager.action-preflight.v1",
            "status": "passed" if all(payload.get(k) for k in required) else "blocked",
            "failures": [f"missing_{k}" for k in required if not payload.get(k)],
            "warnings": []}


def recovery_action_contract(action: dict[str, Any]) -> dict[str, Any]:
    """Receipt, dispatch and semantic completion remain distinct observations."""
    name = action.get("action")
    if name == "task-requeue":
        return {
            "expected_downstream_events": ["task.requeued"],
            "effect_expected_events": ["workflow.call.result.admitted", "task.done", "task.done.accepted"],
        }
    if name != "child-rebuild":
        from zf.runtime.stage_replan_generation import STAGE_REPLAN_GENERATION_EXPECTED_EVENTS
        expected = {
            "stage-retrigger": [],
            "stage-replan-new-generation": sorted(STAGE_REPLAN_GENERATION_EXPECTED_EVENTS),
            "rescan-grant": ["run.goal.rescan.granted"],
        }.get(name, [])
        return {"expected_downstream_events": expected}
    return {
        "expected_downstream_events": ["repair.action.requested"],
        "effect_expected_events": ["fanout.child.completed"],
        "effect_failure_events": ["fanout.child.failed", "repair.action.rejected"],
        "effect_bindings": {"fanout_id": action.get("fanout_id")},
        "continuation_expected_events": ["fanout.child.dispatched"],
    }
