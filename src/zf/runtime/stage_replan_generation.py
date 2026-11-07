"""Shared policy for an owner-approved stage replan generation."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id


STAGE_REPLAN_GENERATION_EXPECTED_EVENTS = frozenset({
    "run.goal.updated",
    "run.manager.action.applied",
})


def completed_generation_source(
    event: ZfEvent, prior: dict[str, ZfEvent], aliases: dict[str, str],
) -> ZfEvent | None:
    """Identify a retired escalation only after a bound recovery succeeds."""
    payload = event.payload or {}
    if (
        event.type != "runtime.action.completed"
        or payload.get("action") != "stage-replan-new-generation"
        or payload.get("status") != "applied"
        or payload.get("replayed")
    ):
        return None
    source = prior.get(str(payload.get("escalation_event_id") or ""))
    trigger = prior.get(str(payload.get("stage_trigger_event_id") or ""))
    goal = prior.get(str(payload.get("run_goal_update_event_id") or ""))
    generation = str(payload.get("stage_replan_generation") or "")
    run_id = str(payload.get("run_id") or "")
    if not source or not trigger or not goal or not generation or not run_id:
        return None
    if (
        source.type != "human.escalate"
        or source.payload.get("failure_class") != "stage_replan_cap_exhausted"
        or goal.type != "run.goal.updated"
        or goal.payload.get("reason") != "authorized_stage_replan_new_generation"
        or trigger.payload.get("operator_authorized") is not True
        or not trigger.payload.get("operator_authorization_ref")
    ):
        return None
    for bound in (goal, trigger):
        if (
            bound.payload.get("stage_replan_generation") != generation
            or bound.payload.get("stage_replan_generation_source_event_id") != source.id
        ):
            return None
    if any(event_run_id(bound, aliases=aliases) != run_id for bound in (source, goal, trigger)):
        return None
    return source


def stage_replan_generation_preflight(
    payload: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    if not str(payload.get("escalation_event_id") or ""):
        failures.append("missing_escalation_event_id")
    if not str(payload.get("approval_ref") or ""):
        failures.append("missing_approval_ref")
    expected = sorted(STAGE_REPLAN_GENERATION_EXPECTED_EVENTS)
    return {
        "schema_version": "run-manager.action-preflight.v1",
        "status": "blocked" if failures else "passed",
        "failures": failures,
        "warnings": [],
        "safe_resume_action": "stage_replan_new_generation",
        "expected_downstream_events": expected,
        "verify_condition": (
            str(payload.get("verify_condition") or "")
            or "expected_downstream_event:" + ",".join(expected)
        ),
    }


__all__ = [
    "STAGE_REPLAN_GENERATION_EXPECTED_EVENTS",
    "stage_replan_generation_preflight",
]
