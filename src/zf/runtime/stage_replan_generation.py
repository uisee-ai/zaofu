"""Shared policy for an owner-approved stage replan generation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.reader_replan_index import ReaderReplanIndex
from zf.runtime.run_scope import event_run_id, run_aliases


STAGE_REPLAN_GENERATION_EXPECTED_EVENTS = frozenset({
    "run.goal.updated",
    "run.manager.action.applied",
})


def escalation_generation_id(run_id: str, escalation_id: str) -> str:
    return "stage-replan-" + hashlib.sha256(f"{run_id}|{escalation_id}".encode()).hexdigest()[:20]


def generation_publication_lock(state_dir: Path, request_id: str) -> Path:
    return state_dir / "locks" / "stage-replan-publications" / hashlib.sha256(request_id.encode()).hexdigest()


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


def completed_owner_goal_updates(events: list[ZfEvent], run: str) -> set[str]:
    """Identify Web goal activations backed by an Owner's completed stage replan."""
    aliases = run_aliases(events)
    prior: dict[str, ZfEvent] = {}
    positions: dict[str, int] = {}
    goals: set[str] = set()
    for index, event in enumerate(events):
        payload = event.payload
        if (
            event.type == "runtime.action.completed" and event.actor == "web"
            and event.origin in {"", "kernel"} and payload.get("run_id") == run
        ):
            source = completed_generation_source(event, prior, aliases)
            if source and _bound_owner_activation(event, source, prior, positions, run):
                goals.add(payload["run_goal_update_event_id"])
        prior[event.id] = event
        positions[event.id] = index
    return goals


def _bound_owner_activation(
    receipt: ZfEvent, escalation: ZfEvent, prior: dict[str, ZfEvent], positions: dict[str, int], run: str,
) -> bool:
    goal = prior[receipt.payload["run_goal_update_event_id"]]
    trigger = prior[receipt.payload["stage_trigger_event_id"]]
    owner = prior.get(goal.causation_id)
    failure = prior.get(escalation.payload.get("source_event_id"))
    if (
        not owner or owner.type != "web.action.requested" or owner.actor != "web"
        or owner.origin not in {"", "kernel"} or owner.payload.get("source_session_id")
        or owner.payload.get("action") != "stage-replan-new-generation"
        or goal.actor != "web" or goal.origin not in {"", "kernel"}
        or trigger.actor != "zf-cli" or trigger.origin not in {"", "kernel"}
        or goal.payload.get("workflow_run_id") != run or trigger.payload.get("workflow_run_id") != run
        or trigger.causation_id != owner.id or receipt.causation_id != trigger.id
        or escalation.actor != "orchestrator" or escalation.origin not in {"", "kernel"}
        or not failure or (escalation.causation_id and escalation.causation_id != failure.id)
        or not positions[failure.id] < positions[escalation.id] < positions[owner.id]
        < positions[goal.id] < positions[trigger.id]
    ):
        return False
    request = owner.payload.get("request")
    return (
        isinstance(request, dict)
        and request.get("workflow_run_id") == run
        and request.get("escalation_event_id") == escalation.id
        and "start_new_generation" in (escalation.payload.get("allowed_actions") or [])
        and goal.payload.get("status") == "active"
        and request.get("approval_ref") == goal.payload.get("approval_ref") == trigger.payload["operator_authorization_ref"]
        and receipt.payload["stage_replan_generation"] == escalation_generation_id(run, escalation.id)
        and receipt.payload.get("source_failure_event_id") == goal.payload.get("source_failure_event_id") == failure.id
    )


def exhausted_failure_escalation(index: ReaderReplanIndex, failure: ZfEvent) -> ZfEvent | None:
    """A stage cap is an owner handoff for this occurrence, across software and generation changes."""
    position = index.positions.get(failure.id)
    if position is None:
        return None
    return next((e for i, e in index.rows("human.escalate")
                 if i > position and e.origin in {"", "kernel"} and e.actor == "orchestrator"
                 and e.payload.get("failure_class") == "stage_replan_cap_exhausted"
                 and e.payload.get("source_event_id") == failure.id
                 and "start_new_generation" in (e.payload.get("allowed_actions") or ())
                 and index.run_id(failure) and index.run_id(e) == index.run_id(failure)), None)


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
