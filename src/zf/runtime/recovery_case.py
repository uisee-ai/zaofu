"""Stable, event-derived recovery case identity and action admission."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from zf.core.events.model import ZfEvent


RECOVERY_CASE_SCHEMA_VERSION = "recovery-case.v1"
RECOVERY_CASE_PROJECTION_SCHEMA_VERSION = "recovery-cases.v1"
RECOVERY_METRICS_SCHEMA_VERSION = "recovery-metrics.v1"
_ACTIVE_STATUSES = frozenset({"applied", "diagnosing", "verifying"})
_TERMINAL_STATUSES = frozenset({"resolved", "superseded", "waiting", "blocked"})
_PROGRESS_EVENTS = frozenset({
    "task_map.ready",
    "task_map.amended",
    "fanout.child.completed",
    "fanout.aggregate.completed",
    "dev.build.done",
    "verify.passed",
    "flow.discovery.completed",
    "flow.goal.closed",
    "module.parity.closed",
    "judge.passed",
    "run.goal.completed",
})


def recovery_case_id_from_payload(
    payload: Mapping[str, Any],
    *,
    fallback: str = "",
) -> str:
    """Return a producer-independent identity for one semantic problem.

    Transport/request/event/incident ids are deliberately excluded. They
    describe observations or deliveries, not the underlying recovery case.
    """

    existing = str(payload.get("recovery_case_id") or "").strip()
    if existing:
        return existing
    envelope = _mapping(payload.get("problem_envelope"))
    run_id = _first(
        payload,
        "workflow_run_id",
        "run_id",
        "pdd_id",
        "feature_id",
        "goal_id",
    )
    task_id = _first(payload, "task_id", "parent_task_id")
    stage_id = _first(payload, "stage_id", "expected_next_stage")
    fanout_id = _first(payload, "fanout_id", "upstream_fanout_id")
    scope_id = task_id or stage_id or fanout_id
    revision = _first(
        payload,
        "contract_revision",
        "request_revision",
        "workflow_generation",
    )
    generation = _first(payload, "task_map_generation", "task_map_digest")
    target = _first(
        payload,
        "target_commit",
        "candidate_head_commit",
        "target_snapshot_digest",
        "target_snapshot_ref",
    )
    failure_scope = _first(payload, "failure_scope", "problem_class") or str(
        envelope.get("problem_class") or ""
    ).strip()
    failure_class = _first(payload, "failure_class", "primary_failure_class") or str(
        envelope.get("failure_class") or ""
    ).strip()
    semantic_fingerprint = _first(
        payload,
        "failure_fingerprint",
        "fingerprint",
        "blocker_fingerprint",
    ) or str(envelope.get("fingerprint") or "").strip()
    if semantic_fingerprint in {
        _first(payload, "source_event_id", "original_trigger_event_id"),
        _first(payload, "checkpoint_id", "idempotency_key"),
    }:
        semantic_fingerprint = ""

    identity = {
        "workflow_run_id": run_id,
        "scope_id": scope_id,
        "contract_revision": revision,
        "task_map_generation": generation,
        "failure_scope": failure_scope,
        "failure_class": failure_class,
        "fingerprint": semantic_fingerprint,
        "target": target,
    }
    if not any(identity.values()):
        identity["fallback"] = str(fallback or "unknown-recovery-case").strip()
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"rcase-{digest[:16]}"


def bind_recovery_case(action: Mapping[str, Any]) -> dict[str, Any]:
    """Attach stable case/action-family identity to one pending action."""

    updated = dict(action)
    case_id = recovery_case_id_from_payload(updated)
    action_name = str(updated.get("action") or updated.get("safe_resume_action") or "recover")
    action_family = _action_family(action_name)
    updated.update({
        "recovery_case_id": case_id,
        "recovery_action_family": action_family,
    })
    return updated


def converge_recovery_actions(
    events: Iterable[ZfEvent],
    actions: Iterable[Mapping[str, Any]],
    *,
    no_progress_limit: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select at most one executable action per open recovery case."""

    event_list = list(events)
    projection = build_recovery_case_projection(event_list)
    by_id = {
        str(item.get("recovery_case_id") or ""): item
        for item in projection.get("cases", [])
        if isinstance(item, Mapping)
    }
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    suppressed: list[dict[str, str]] = []
    for raw in actions:
        action = bind_recovery_case(raw)
        case_id = action["recovery_case_id"]
        state = by_id.get(case_id, {})
        status = str(state.get("status") or "open")
        failure_count = int(state.get("consecutive_failures") or 0)
        reason = ""
        if case_id in seen:
            reason = "duplicate_pending_action"
        elif status in _ACTIVE_STATUSES:
            reason = "active_effect"
        elif status in _TERMINAL_STATUSES:
            reason = f"case_{status}"
        elif failure_count >= max(1, int(no_progress_limit)):
            reason = "case_no_progress_limit"
        if reason:
            suppressed.append({"recovery_case_id": case_id, "reason": reason})
            continue
        seen.add(case_id)
        action["recovery_admission"] = {
            "schema_version": "recovery-action-admission.v1",
            "status": "admitted",
            "recovery_case_id": case_id,
            "case_status": status,
            "consecutive_failures": failure_count,
        }
        selected.append(action)
    projection = {
        **projection,
        "suppressed_actions": suppressed,
        "summary": {
            **_mapping(projection.get("summary")),
            "pending_admitted": len(selected),
            "pending_suppressed": len(suppressed),
        },
    }
    return selected, projection


def recovery_action_admission(
    events: Iterable[ZfEvent],
    action: Mapping[str, Any],
    *,
    no_progress_limit: int = 3,
) -> dict[str, Any]:
    """Re-check one action immediately before its external side effect."""

    bound = bind_recovery_case(action)
    case_id = bound["recovery_case_id"]
    projection = build_recovery_case_projection(events)
    state = next(
        (
            item
            for item in projection.get("cases", [])
            if isinstance(item, Mapping)
            and str(item.get("recovery_case_id") or "") == case_id
        ),
        {},
    )
    status = str(state.get("status") or "open")
    failures = int(state.get("consecutive_failures") or 0)
    reason = ""
    if status in _ACTIVE_STATUSES:
        reason = "active_effect"
    elif status in _TERMINAL_STATUSES:
        reason = f"case_{status}"
    elif failures >= max(1, int(no_progress_limit)):
        reason = "case_no_progress_limit"
    return {
        "schema_version": "recovery-action-admission.v1",
        "status": "blocked" if reason else "admitted",
        "reason": reason,
        "recovery_case_id": case_id,
        "case_status": status,
        "consecutive_failures": failures,
    }


def build_recovery_case_projection(events: Iterable[ZfEvent]) -> dict[str, Any]:
    """Fold recovery lifecycle from append-only events without writing truth."""

    cases: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = _mapping(event.payload)
        explicit = str(payload.get("recovery_case_id") or "").strip()
        if not explicit:
            continue
        case = cases.setdefault(explicit, {
            "schema_version": RECOVERY_CASE_SCHEMA_VERSION,
            "recovery_case_id": explicit,
            "status": "open",
            "workflow_run_id": _first(payload, "workflow_run_id", "run_id"),
            "task_id": str(event.task_id or payload.get("task_id") or ""),
            "task_map_generation": str(payload.get("task_map_generation") or ""),
            "target_commit": _first(payload, "target_commit", "candidate_head_commit"),
            "failure_class": str(payload.get("failure_class") or ""),
            "last_event_id": "",
            "last_event_type": "",
            "action_count": 0,
            "consecutive_failures": 0,
        })
        case["last_event_id"] = event.id
        case["last_event_type"] = event.type
        event_type = event.type
        if event_type == "run.manager.action.planned":
            case["action_count"] += 1
            case["status"] = "action_planned"
        elif event_type == "run.manager.action.applied":
            case["status"] = "applied"
        elif event_type in {
            "run.manager.autoresearch.requested",
            "autoresearch.invocation.requested",
            "autoresearch.loop.requested",
            "autoresearch.loop.started",
        }:
            case["status"] = "diagnosing"
        elif event_type == "run.manager.action.effect.pending":
            case["status"] = "verifying"
        elif event_type in {
            "run.manager.action.verify.passed",
            "run.manager.action.effect.passed",
        }:
            if event_type == "run.manager.action.verify.passed" and (
                payload.get("effect_expected_events")
                or payload.get("effect_required_sequence")
                or payload.get("action") in {"semantic-replan-request", "worker-lifecycle-recover"}
            ):
                case["status"] = "verifying"
            else:
                case["status"] = "resolved"
                case["consecutive_failures"] = 0
        elif event_type in {
            "run.manager.action.failed",
            "run.manager.action.verify.failed",
            "run.manager.action.effect.failed",
            "autoresearch.loop.failed",
        }:
            case["status"] = "open"
            case["consecutive_failures"] += 1
        elif event_type == "run.manager.action.no_progress_break":
            case["status"] = "blocked"
        elif event_type == "run.manager.action.blocked":
            case["status"] = (
                "waiting"
                if str(payload.get("blocker_kind") or "") in {
                    "external_gate",
                    "human_decision",
                    "provider",
                    "environment",
                    "capability",
                }
                else "blocked"
            )
        elif event_type in {
            "autoresearch.loop.completed",
            "run.manager.autoresearch.consumed",
        }:
            next_route = str(payload.get("next_route") or payload.get("status") or "")
            if next_route in {"failed", "human_escalate", "repair_worker", "proposal_review"}:
                case["status"] = "open"
            else:
                case["status"] = "resolved"
                case["consecutive_failures"] = 0
        elif event_type in _PROGRESS_EVENTS and case["status"] != "verifying":
            case["status"] = "resolved"
            case["consecutive_failures"] = 0

    rows = sorted(cases.values(), key=lambda item: str(item["recovery_case_id"]))
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "open")
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": RECOVERY_CASE_PROJECTION_SCHEMA_VERSION,
        "is_derived_projection": True,
        "cases": rows,
        "summary": {
            "total": len(rows),
            "active": sum(counts.get(item, 0) for item in _ACTIVE_STATUSES),
            "by_status": dict(sorted(counts.items())),
        },
    }


def build_recovery_metrics(events: Iterable[ZfEvent]) -> dict[str, Any]:
    """Classify effective recovery work without counting observer chatter.

    Run Manager triage, diagnosis, recommendation, and action-planning events
    intentionally do not increment these counters. Each category is keyed by
    the identity of the effect it represents so replayed event deliveries do
    not look like additional product failures.
    """

    rows: dict[str, list[dict[str, str]]] = {
        "semantic_rework": [],
        "protocol_repair": [],
        "environment_retry": [],
        "external_wait": [],
        "superseded": [],
    }
    seen: dict[str, set[str]] = {name: set() for name in rows}
    for event in events:
        category = _recovery_metric_category(event)
        if not category:
            continue
        key = _recovery_metric_key(event, category)
        if key in seen[category]:
            continue
        seen[category].add(key)
        payload = _mapping(event.payload)
        rows[category].append({
            "event_id": str(event.id or ""),
            "event_type": event.type,
            "identity": key,
            "task_id": str(event.task_id or payload.get("task_id") or ""),
            "recovery_case_id": str(payload.get("recovery_case_id") or ""),
        })
    counts = {name: len(items) for name, items in rows.items()}
    return {
        "schema_version": RECOVERY_METRICS_SCHEMA_VERSION,
        "is_derived_projection": True,
        "counts": counts,
        "total_effects": sum(counts.values()),
        "effects": rows,
    }


def _recovery_metric_category(event: ZfEvent) -> str:
    payload = _mapping(event.payload)
    if event.type in {"task.rework.requested", "replan.adoption.completed"}:
        return "semantic_rework"
    if event.type == "workflow.call.result.repair.requested":
        return "protocol_repair"
    if event.type == "workflow.operation.retry_started":
        return "environment_retry"
    if event.type == "task.attempt.retry_scheduled" and _is_external_or_environment(payload):
        return "environment_retry"
    if event.type == "run.goal.completion.blocked" and (
        isinstance(payload.get("external_wait"), Mapping)
        or "waiting_external" in {
            str(item or "") for item in payload.get("blockers") or []
        }
    ):
        return "external_wait"
    if event.type == "run.manager.action.blocked" and _is_external_or_environment(payload):
        return "external_wait"
    if event.type in {
        "workflow.operation.superseded",
        "task.attempt.superseded",
        "fanout.child.stale_completion",
        "goal.closure.superseded",
    }:
        return "superseded"
    if str(payload.get("reason") or "") == "stale_call_result_superseded":
        return "superseded"
    return ""


def _is_external_or_environment(payload: Mapping[str, Any]) -> bool:
    values = {
        str(payload.get("blocker_kind") or "").strip().lower(),
        str(payload.get("failure_class") or "").strip().lower(),
        str(payload.get("problem_class") or "").strip().lower(),
        str(payload.get("recovery_class") or "").strip().lower(),
    }
    external_wait = payload.get("external_wait")
    if isinstance(external_wait, Mapping):
        values.add(str(external_wait.get("kind") or "").strip().lower())
        values.add(str(external_wait.get("status") or "").strip().lower())
    markers = ("environment", "provider", "external", "capability", "transport")
    return any(any(marker in value for marker in markers) for value in values if value)


def _recovery_metric_key(event: ZfEvent, category: str) -> str:
    payload = _mapping(event.payload)
    fields_by_category = {
        "semantic_rework": (
            "workflow_run_id",
            "task_id",
            "task_map_generation",
            "recovery_case_id",
            "failure_fingerprint",
            "attempt",
            "candidate_task_map_ref",
            "new_task_map_ref",
        ),
        "protocol_repair": (
            "operation_id",
            "request_hash",
            "repair_round",
        ),
        "environment_retry": (
            "operation_id",
            "active_attempt_id",
            "retry_attempt",
            "attempt_id",
        ),
        "external_wait": (
            "workflow_run_id",
            "run_id",
            "claim_id",
            "blocker_fingerprint",
            "provider_qualification_receipt_digest",
        ),
        "superseded": (
            "operation_id",
            "request_hash",
            "attempt_id",
            "fanout_id",
            "task_map_generation",
            "superseded_event_id",
        ),
    }
    values = [
        f"{field}={str(payload.get(field) or '').strip()}"
        for field in fields_by_category[category]
        if str(payload.get(field) or "").strip()
    ]
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    if task_id and not any(value.startswith("task_id=") for value in values):
        values.append(f"task_id={task_id}")
    if not values:
        values.append(f"event_id={event.id or event.type}")
    return f"{category}:" + "|".join(values)


def _action_family(action: str) -> str:
    value = str(action or "").strip().lower().replace("_", "-")
    if "autoresearch" in value or "diagnos" in value:
        return "diagnose"
    if "replan" in value or "triage" in value:
        return "replan"
    if "rework" in value or "repair" in value:
        return "rework"
    if "resume" in value or "recover" in value or "redrive" in value:
        return "resume"
    if "human" in value or "approval" in value:
        return "human"
    return value or "recover"


def _first(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "RECOVERY_CASE_PROJECTION_SCHEMA_VERSION",
    "RECOVERY_CASE_SCHEMA_VERSION",
    "RECOVERY_METRICS_SCHEMA_VERSION",
    "bind_recovery_case",
    "build_recovery_case_projection",
    "build_recovery_metrics",
    "converge_recovery_actions",
    "recovery_action_admission",
    "recovery_case_id_from_payload",
]
