"""Replay-safe run continuation projection and operation identity."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import events_for_run


RUN_CONTINUATION_SCHEMA_VERSION = "run-continuation.v1"

_PROGRESS_EVENTS = frozenset({
    "workflow.invoke.requested",
    "task_map.ready",
    "task_map.amended",
    "task_map.admitted",
    "goal.claim_set.pinned",
    "task.assigned",
    "task.dispatched",
    "task.done",
    "fanout.started",
    "fanout.child.dispatched",
    "fanout.child.completed",
    "fanout.aggregate.completed",
    "candidate.ready",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
    "run.delivery.requested",
    "run.delivery.settled",
    "ship.completed",
    "ship.done",
    "run.goal.completed",
    "run.goal.blocked",
    "run.failed",
    "run.cancelled",
})

_PHASES = (
    ("terminal", frozenset({
        "run.goal.completed",
        "run.goal.blocked",
        "run.completed",
        "run.failed",
        "run.cancelled",
    })),
    ("delivery", frozenset({
        "run.delivery.requested",
        "run.delivery.settled",
        "ship.started",
        "ship.completed",
        "ship.done",
    })),
    ("judge", frozenset({"judge.passed", "judge.failed"})),
    ("verification", frozenset({
        "review.approved",
        "review.rejected",
        "verify.passed",
        "verify.failed",
        "test.passed",
        "test.failed",
    })),
    ("candidate", frozenset({
        "candidate.ready",
        "candidate.quality.failed",
        "integration.failed",
    })),
    ("execution", frozenset({
        "task.assigned",
        "task.dispatched",
        "task.done",
        "fanout.started",
        "fanout.child.dispatched",
        "fanout.child.completed",
        "fanout.child.failed",
        "fanout.aggregate.completed",
    })),
    ("admission", frozenset({
        "workflow.invoke.requested",
        "task_map.ready",
        "task_map.amended",
        "task_map.admitted",
        "goal.claim_set.pinned",
    })),
)


def progress_digest(events: Iterable[ZfEvent]) -> str:
    facts: list[dict[str, str]] = []
    for event in events:
        if event.type not in _PROGRESS_EVENTS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        facts.append({
            "id": str(event.id or ""),
            "type": event.type,
            "task_id": str(event.task_id or payload.get("task_id") or ""),
            "generation": str(
                payload.get("task_map_generation")
                or payload.get("generation")
                or ""
            ),
            "status": str(payload.get("status") or ""),
        })
    encoded = json.dumps(
        facts,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def operation_key_for_action(
    action: dict[str, Any],
    *,
    run_id: str,
    generation: str,
) -> str:
    scope = {
        key: str(action.get(key) or "")
        for key in (
            "task_id",
            "workflow_run_id",
            "run_id",
            "pdd_id",
            "feature_id",
            "fanout_id",
            "stage_id",
            "trace_id",
        )
        if str(action.get(key) or "")
    }
    envelope = action.get("problem_envelope")
    envelope = envelope if isinstance(envelope, dict) else {}
    identity = {
        "run_id": run_id,
        "generation": generation,
        "scope": scope,
        "action": str(action.get("action") or ""),
        "safe_resume_action": str(action.get("safe_resume_action") or ""),
        "checkpoint_id": str(action.get("checkpoint_id") or ""),
        "failure_fingerprint": str(
            action.get("fingerprint")
            or envelope.get("fingerprint")
            or action.get("failure_class")
            or ""
        ),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "op-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def enrich_continuation_actions(
    actions: list[dict[str, Any]],
    *,
    run_id: str,
    generation: str,
    current_progress_digest: str,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for action in actions:
        updated = dict(action)
        updated["operation_key"] = operation_key_for_action(
            updated,
            run_id=run_id,
            generation=generation,
        )
        updated["operation_precondition"] = {
            "run_id": run_id,
            "generation": generation,
            "progress_digest": current_progress_digest,
            "preflight_status": str(
                ((updated.get("preflight") or {}).get("status") or "")
                if isinstance(updated.get("preflight"), dict)
                else ""
            ),
        }
        attempt_cap = int(updated.get("attempt_cap") or 1)
        updated["operation_attempt_cap"] = attempt_cap
        explicit_deadline = str(
            updated.get("deadline") or updated.get("expires_at") or ""
        )
        updated["operation_deadline"] = (
            {"kind": "timestamp", "value": explicit_deadline}
            if explicit_deadline
            else {"kind": "attempt_cap", "max_attempts": attempt_cap}
        )
        decision = updated.get("policy_decision")
        decision = decision if isinstance(decision, dict) else {}
        updated["operation_terminal_fallback"] = (
            "human_or_blocked"
            if str(decision.get("decision") or "")
            in {"needs_approval", "human_escalate", "safe_halt"}
            else "diagnose_then_block"
        )
        enriched.append(updated)
    return enriched


def build_run_continuation_projection(
    events: list[ZfEvent],
    *,
    goal: dict[str, Any],
    pending_actions: list[dict[str, Any]],
    completion_profile: dict[str, Any],
) -> dict[str, Any]:
    run_id = str(goal.get("run_id") or "")
    scoped_events = events_for_run(events, run_id=run_id) if run_id else list(events)
    generation = _latest_generation(scoped_events)
    digest = progress_digest(scoped_events)
    terminal_status, terminal_event_id = _terminal_status(scoped_events, goal)
    phase = "terminal" if terminal_status else _latest_phase(scoped_events)
    next_operation: dict[str, Any] | None = None
    if not terminal_status and pending_actions:
        selected = pending_actions[0]
        next_operation = {
            key: selected.get(key)
            for key in (
                "operation_key",
                "action",
                "safe_resume_action",
                "checkpoint_id",
                "task_id",
                "pdd_id",
                "feature_id",
                "fanout_id",
                "failure_class",
                "owner_route",
                "operation_precondition",
                "operation_attempt_cap",
                "operation_deadline",
                "operation_terminal_fallback",
            )
            if selected.get(key) not in (None, "")
        }
    status = terminal_status or ("active" if run_id or pending_actions else "idle")
    return {
        "schema_version": RUN_CONTINUATION_SCHEMA_VERSION,
        "is_derived_projection": True,
        "run_id": run_id,
        "generation": generation,
        "status": status,
        "phase": phase,
        "terminal": bool(terminal_status),
        "terminal_event_id": terminal_event_id,
        "progress_digest": digest,
        "completion_status": str(completion_profile.get("status") or "unknown"),
        "next_operation": next_operation,
        "pending_operation_count": len(pending_actions),
    }


def _latest_generation(events: list[ZfEvent]) -> str:
    for event in reversed(events):
        if event.type not in {
            "goal.claim_set.pinned",
            "task_map.ready",
            "task_map.amended",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        generation = str(
            payload.get("task_map_generation")
            or payload.get("generation")
            or payload.get("task_map_digest")
            or ""
        )
        if generation:
            return generation
    return ""


def _terminal_status(
    events: list[ZfEvent],
    goal: dict[str, Any],
) -> tuple[str, str]:
    status_events = {
        "run.goal.completed": "completed",
        "run.completed": "completed",
        "run.goal.blocked": "blocked",
        "run.failed": "failed",
        "run.cancelled": "cancelled",
    }
    terminal_status = ""
    terminal_event_id = ""
    for event in events:
        if event.type in {"run.goal.started", "run.goal.updated"}:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "run.goal.started" or str(payload.get("status") or "") in {
                "active",
                "running",
            }:
                terminal_status = ""
                terminal_event_id = ""
        mapped = status_events.get(event.type)
        if mapped:
            terminal_status = mapped
            terminal_event_id = event.id
    if not terminal_status:
        goal_status = str(goal.get("status") or "")
        if goal_status == "complete":
            terminal_status = "completed"
        elif goal_status == "blocked":
            terminal_status = "blocked"
        if terminal_status:
            terminal_event_id = str(goal.get("source_event_id") or "")
    return terminal_status, terminal_event_id


def _latest_phase(events: list[ZfEvent]) -> str:
    for event in reversed(events):
        for phase, event_types in _PHASES:
            if event.type in event_types:
                return phase
    return "intake"
