"""Unified runtime problem taxonomy.

The taxonomy is a projection helper. It normalizes Autoresearch failure
signals, Supervisor attention items, and Run Manager pending actions into one
small envelope without mutating kernel truth or replacing existing fine-grained
failure classes.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.event_problem_registry import (
    abnormal_event_specs,
    expected_negative_event_types,
    spec_for_event,
)


PROBLEM_ENVELOPE_SCHEMA_VERSION = "runtime.problem-envelope.v1"

PROBLEM_CLASSES = frozenset({
    "runtime_liveness",
    "workflow_progress",
    "worker_lifecycle",
    "artifact_contract",
    "candidate_quality",
    "source_repair",
    "external_gate",
    "product_gap",
    "unknown",
})

EXPECTED_NEGATIVE_EVENT_TYPES = expected_negative_event_types()

_ABNORMAL_EVENT_RULES: dict[str, dict[str, Any]] = {
    event_type: {
        "source": spec.source,
        "severity": spec.severity,
        "title": spec.title,
        "failure_class": spec.failure_class,
        "owner_route": spec.owner_route,
        "action_policy": spec.action_policy,
        "intervention_class": spec.intervention_class,
        "suggested_route": spec.suggested_route,
        "action_kind": spec.suggested_action_kind,
        "notification_policy": spec.effective_notification_policy,
        "recovery_policy": spec.effective_recovery_policy,
        "dedupe_key_fields": spec.dedupe_key_fields,
        "human_required_when": spec.human_required_when,
    }
    for event_type, spec in abnormal_event_specs().items()
}

_PROBLEM_CLASS_DEFAULTS: dict[str, dict[str, str]] = {
    "runtime_liveness": {
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
    },
    "workflow_progress": {
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
    },
    "worker_lifecycle": {
        "owner_route": "controlled_action",
        "action_policy": "auto_decide",
        "intervention_class": "auto_recover",
    },
    "artifact_contract": {
        "owner_route": "orchestrator_replan",
        "action_policy": "needs_diagnosis",
        "intervention_class": "semantic_replan",
    },
    "candidate_quality": {
        "owner_route": "orchestrator_replan",
        "action_policy": "needs_diagnosis",
        "intervention_class": "semantic_replan",
    },
    "source_repair": {
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "repair_harness",
    },
    "external_gate": {
        "owner_route": "human",
        "action_policy": "human_escalate",
        "intervention_class": "human_decision",
    },
    "product_gap": {
        "owner_route": "orchestrator_replan",
        "action_policy": "needs_diagnosis",
        "intervention_class": "semantic_replan",
    },
    "unknown": {
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
    },
}


def problem_envelope_from_failure_signal(signal: Any) -> dict[str, Any]:
    """Build a problem envelope from an Autoresearch FailureSignal-like row."""

    row = _as_mapping(signal)
    return build_problem_envelope(
        source=str(row.get("source_kind") or "autoresearch"),
        fingerprint=str(row.get("fingerprint") or row.get("signal_id") or ""),
        severity=str(row.get("severity") or "medium"),
        title=str(row.get("summary") or row.get("category") or "failure signal"),
        summary=str(row.get("summary") or ""),
        task_id=str(row.get("task_id") or ""),
        failure_class=str(row.get("category") or row.get("failure_class") or ""),
        source_event_ids=_string_list(row.get("event_ids")),
        source_ref=str(row.get("source_path") or ""),
        source_refs=_string_list(row.get("evidence_paths")),
        confidence="derived",
    )


def problem_envelope_from_attention(item: dict[str, Any]) -> dict[str, Any]:
    """Build a problem envelope from a Supervisor attention item."""

    existing = item.get("problem_envelope")
    if isinstance(existing, dict) and existing.get("schema_version"):
        return redact_obj(existing)
    return build_problem_envelope(
        source=str(item.get("source") or "supervisor"),
        fingerprint=str(item.get("fingerprint") or item.get("attention_id") or ""),
        severity=str(item.get("severity") or "medium"),
        title=str(item.get("title") or ""),
        summary=str(item.get("summary") or item.get("message") or ""),
        task_id=str(item.get("task_id") or ""),
        failure_class=str(
            item.get("failure_class")
            or item.get("primary_failure_class")
            or ""
        ),
        owner_route=str(item.get("owner_route") or ""),
        action_policy=str(item.get("action_policy") or ""),
        intervention_class=str(item.get("intervention_class") or ""),
        suggested_route=str(item.get("suggested_route") or ""),
        recommended_route=str(item.get("recommended_route") or ""),
        suggested_action=_safe_mapping(item.get("suggested_action")),
        source_event_ids=_string_list(item.get("source_event_ids")),
        source_ref=str(item.get("source_ref") or ""),
        source_refs=_string_list(item.get("source_refs")),
        allowed_actions=_string_list(item.get("allowed_actions")),
        verify_condition=str(item.get("verify_condition") or ""),
        confidence=str(item.get("confidence") or "derived"),
    )


def problem_envelope_from_action(action: dict[str, Any]) -> dict[str, Any]:
    """Build a problem envelope from a Run Manager pending action."""

    existing = action.get("problem_envelope")
    if isinstance(existing, dict) and existing.get("schema_version"):
        return redact_obj(existing)
    decision = action.get("policy_decision")
    decision = decision if isinstance(decision, dict) else {}
    return build_problem_envelope(
        source=str(action.get("source") or "run_manager"),
        fingerprint=str(
            action.get("fingerprint")
            or action.get("checkpoint_id")
            or action.get("idempotency_key")
            or ""
        ),
        severity=str(action.get("severity") or "medium"),
        title=str(action.get("title") or action.get("action") or ""),
        summary=str(action.get("summary") or action.get("reason") or ""),
        task_id=str(action.get("task_id") or ""),
        failure_class=str(
            action.get("failure_class")
            or decision.get("failure_class")
            or ""
        ),
        owner_route=str(action.get("owner_route") or decision.get("owner_route") or ""),
        action_policy=str(
            action.get("action_policy") or decision.get("action_policy") or ""
        ),
        intervention_class=str(
            action.get("intervention_class")
            or decision.get("intervention_class")
            or ""
        ),
        suggested_route=str(action.get("suggested_route") or ""),
        suggested_action=_safe_mapping(action.get("suggested_action")),
        source_event_ids=_string_list(action.get("source_event_ids")),
        source_ref=str(action.get("source_ref") or ""),
        source_refs=_string_list(action.get("source_refs")),
        allowed_actions=_action_allowed_actions(action),
        verify_condition=str(
            action.get("verify_condition")
            or decision.get("verify_condition")
            or ""
        ),
        confidence=str(action.get("confidence") or "derived"),
    )


def abnormal_event_projection(event: Any) -> dict[str, Any] | None:
    """Return normalized fields for an actionable abnormal runtime event.

    Expected-negative workflow branch events are intentionally excluded here:
    a single ``verify.failed`` or ``review.rejected`` is part of normal rework
    flow and should not bypass the deterministic graph. Repeated or unhandled
    branch failures are handled by Supervisor detectors that have enough
    context to decide they are actionable.
    """

    row = _event_row(event)
    event_type = str(row.get("type") or "").strip()
    if not event_type or event_type in EXPECTED_NEGATIVE_EVENT_TYPES:
        return None
    rule = _ABNORMAL_EVENT_RULES.get(event_type)
    if not rule:
        return None
    payload = _safe_mapping(row.get("payload"))
    event_id = str(row.get("id") or "")
    task_id = str(row.get("task_id") or payload.get("task_id") or "")
    source = str(rule.get("source") or "runtime_event")
    fingerprint = str(
        payload.get("fingerprint")
        or payload.get("checkpoint_id")
        or payload.get("attention_id")
        or _event_policy_fingerprint(event_type, row, payload, rule)
    )
    failure_class = str(rule.get("failure_class") or event_type.replace(".", "_"))
    summary = _event_summary(payload, default=f"{event_type} requires runtime diagnosis")
    suggested_action = {
        "kind": str(rule.get("action_kind") or "diagnose_runtime_event"),
        "event_type": event_type,
        "scope": _event_scope(row, payload),
    }
    envelope = build_problem_envelope(
        source=source,
        fingerprint=fingerprint,
        severity=str(rule.get("severity") or payload.get("severity") or "medium"),
        title=str(payload.get("title") or rule.get("title") or event_type),
        summary=summary,
        task_id=task_id,
        failure_class=failure_class,
        owner_route=str(rule.get("owner_route") or ""),
        action_policy=str(rule.get("action_policy") or ""),
        intervention_class=str(rule.get("intervention_class") or ""),
        suggested_route=str(rule.get("suggested_route") or "run_manager_recovery"),
        suggested_action=suggested_action,
        source_event_ids=[event_id] if event_id else [],
        source_ref=f"events.jsonl#{event_id}" if event_id else "",
        confidence="event_registry",
    )
    return redact_obj({
        "source": source,
        "fingerprint": fingerprint,
        "severity": str(envelope.get("severity") or "medium"),
        "title": str(payload.get("title") or rule.get("title") or event_type),
        "summary": summary,
        "task_id": task_id,
        "failure_class": failure_class,
        "source_event_ids": [event_id] if event_id else [],
        "source_ref": f"events.jsonl#{event_id}" if event_id else "",
        "suggested_route": str(rule.get("suggested_route") or "run_manager_recovery"),
        "suggested_action": suggested_action,
        "owner_route": str(rule.get("owner_route") or ""),
        "action_policy": str(rule.get("action_policy") or ""),
        "intervention_class": str(rule.get("intervention_class") or ""),
        "notification_policy": str(rule.get("notification_policy") or ""),
        "recovery_policy": str(rule.get("recovery_policy") or ""),
        "dedupe_key_fields": _string_list(rule.get("dedupe_key_fields")),
        "human_required_when": _string_list(rule.get("human_required_when")),
        "problem_envelope": envelope,
    })


def problem_envelope_from_event(event: Any) -> dict[str, Any] | None:
    """Build a problem envelope directly from a known abnormal event."""

    projection = abnormal_event_projection(event)
    if projection is not None:
        envelope = projection.get("problem_envelope")
        return redact_obj(envelope) if isinstance(envelope, dict) else None
    row = _event_row(event)
    event_type = str(row.get("type") or "").strip()
    if event_type not in EXPECTED_NEGATIVE_EVENT_TYPES:
        return None
    spec = spec_for_event(event_type)
    payload = _safe_mapping(row.get("payload"))
    event_id = str(row.get("id") or "")
    failure_class = (
        spec.failure_class if spec is not None and spec.failure_class
        else event_type.replace(".", "_")
    )
    action_kind = (
        spec.suggested_action_kind if spec is not None and spec.suggested_action_kind
        else "follow_workflow_rework"
    )
    source = spec.source if spec is not None and spec.source else "workflow_branch"
    suggested_route = (
        spec.suggested_route if spec is not None and spec.suggested_route
        else "workflow_rework"
    )
    return build_problem_envelope(
        source=source,
        fingerprint=str(
            payload.get("fingerprint")
            or f"{event_type}:{_event_scope(row, payload)}"
        ),
        severity=str(
            payload.get("severity")
            or (spec.severity if spec is not None and spec.severity else "medium")
        ),
        title=str(
            payload.get("title")
            or (spec.title if spec is not None and spec.title else event_type)
        ),
        summary=_event_summary(payload, default=f"{event_type} entered workflow branch"),
        task_id=str(row.get("task_id") or payload.get("task_id") or ""),
        failure_class=failure_class,
        owner_route=spec.owner_route if spec is not None else "",
        action_policy=spec.action_policy if spec is not None else "",
        intervention_class=spec.intervention_class if spec is not None else "",
        suggested_route=suggested_route,
        suggested_action={"kind": action_kind, "event_type": event_type},
        source_event_ids=[event_id] if event_id else [],
        source_ref=f"events.jsonl#{event_id}" if event_id else "",
        confidence="event_registry",
    )


def build_problem_envelope(
    *,
    source: str,
    fingerprint: str,
    severity: str,
    title: str = "",
    summary: str = "",
    task_id: str = "",
    failure_class: str = "",
    owner_route: str = "",
    action_policy: str = "",
    intervention_class: str = "",
    suggested_route: str = "",
    recommended_route: str = "",
    suggested_action: dict[str, Any] | None = None,
    source_event_ids: list[str] | None = None,
    source_ref: str = "",
    source_refs: list[str] | None = None,
    allowed_actions: list[str] | tuple[str, ...] | None = None,
    verify_condition: str = "",
    confidence: str = "derived",
) -> dict[str, Any]:
    """Return a normalized problem envelope.

    ``failure_class`` remains the detailed detector/router class. ``problem_class``
    is the shared coarse bucket used across runtime subsystems.
    """

    action = _safe_mapping(suggested_action)
    detail_class = _detail_failure_class(
        failure_class=failure_class,
        source=source,
        suggested_route=suggested_route,
        recommended_route=recommended_route,
        suggested_action=action,
        title=title,
        summary=summary,
        fingerprint=fingerprint,
    )
    problem_class = problem_class_for(
        failure_class=detail_class,
        source=source,
        suggested_route=suggested_route,
        recommended_route=recommended_route,
        suggested_action=action,
        title=title,
        summary=summary,
        fingerprint=fingerprint,
    )
    defaults = _PROBLEM_CLASS_DEFAULTS[problem_class]
    action_names = _string_list(allowed_actions)
    action_kind = str(action.get("kind") or "")
    if action_kind and action_kind not in action_names:
        action_names.append(action_kind)
    if not action_names:
        action_names = _default_allowed_actions(problem_class)
    refs = _string_list(source_refs)
    if source_ref and source_ref not in refs:
        refs.insert(0, source_ref)
    envelope = {
        "schema_version": PROBLEM_ENVELOPE_SCHEMA_VERSION,
        "problem_id": _problem_id(
            source=source,
            fingerprint=fingerprint,
            failure_class=detail_class,
            problem_class=problem_class,
        ),
        "problem_class": problem_class,
        "failure_class": detail_class,
        "severity": _severity(severity),
        "source": source or "unknown",
        "fingerprint": fingerprint,
        "title": title,
        "summary": summary,
        "task_id": task_id,
        "owner_route": owner_route or defaults["owner_route"],
        "action_policy": action_policy or defaults["action_policy"],
        "intervention_class": intervention_class or defaults["intervention_class"],
        "suggested_route": suggested_route,
        "recommended_route": recommended_route,
        "allowed_actions": sorted(set(action_names)),
        "verify_condition": verify_condition,
        "source_event_ids": _string_list(source_event_ids),
        "source_refs": refs,
        "confidence": confidence or "derived",
    }
    return redact_obj(envelope)


def problem_class_for(
    *,
    failure_class: str = "",
    source: str = "",
    suggested_route: str = "",
    recommended_route: str = "",
    suggested_action: dict[str, Any] | None = None,
    title: str = "",
    summary: str = "",
    fingerprint: str = "",
) -> str:
    text = " ".join([
        failure_class,
        source,
        suggested_route,
        recommended_route,
        str(_safe_mapping(suggested_action).get("kind") or ""),
        title,
        summary,
        fingerprint,
    ]).lower()
    if any(token in text for token in (
        "worker_lifecycle",
        "worker_stuck",
        "worker.stuck",
        "worker-lifecycle",
        "worker lifecycle",
        "worker_respawn",
        "worker_recycle",
        "resident_agent_stalled",
        "resident-agent",
    )):
        return "worker_lifecycle"
    if any(token in text for token in (
        "workflow_resume",
        "workflow-resume",
        "fanout",
        "handoff_stall",
        "terminal_missing",
        "worker_noop",
        "deterministic_resume",
        "deterministic_task_resume",
        "task_ref_handoff",
        "stage_dispatch",
        "gate_dispatch",
        "dispatch_silent_stall",
        "orchestrator_dispatch_failed",
    )):
        return "workflow_progress"
    if any(token in text for token in (
        "runtime_fatal",
        "runtime_tick",
        "tick_failed",
        "orchestrator_pane_dead",
        "pane_dead",
        "web_bind_failure",
        "supervisor_projection_stale",
        "no_progress",
        "unknown_runtime_gap",
        "dispatch_preflight_blocker",
        "cost_budget_exceeded",
        "cost.budget.exceeded",
        "budget_exceeded",
    )):
        return "runtime_liveness"
    if any(token in text for token in (
        "task_map",
        "source_refs",
        "source_ref",
        "contract",
        "control_plane_violation",
        "completion_without_gate",
        "self_declared_completion",
        "state_dir_violation",
        "readonly_gate_mutation",
        "replan_followthrough",
        "refactor_plan_blocked",
        "refactor_review_blocked",
    )):
        return "artifact_contract"
    if any(token in text for token in (
        "candidate_quality",
        "candidate_rework",
        "verify.failed",
        "verify_failed",
        "integration.failed",
        "integration_failed",
        "test.failed",
        "test_failed",
        "review.rejected",
        "review_rejected",
        "judge.failed",
        "judge_failed",
        "static_gate.failed",
        "static_gate_failed",
        "missing_real_provider_evidence",
        "verification_environment_missing_tool",
        "evaluator_drift",
    )):
        return "candidate_quality"
    if any(token in text for token in (
        "zaofu_bug",
        "source_repair",
        "self_repair",
        "repair_harness",
        "repair_project",
        "repair-closeout",
        "operator_access_bug",
    )):
        return "source_repair"
    if any(token in text for token in (
        "human",
        "owner_visible",
        "owner.visible",
        "feishu",
        "inbox",
        "approval",
        "external_gate",
        "blocked_external_gate",
    )):
        return "external_gate"
    if any(token in text for token in (
        "product_gap",
        "parity",
        "codex_realism_gap",
        "feature_gap",
        "flow_discovery_failed",
        "flow_goal_blocked",
        "goal_rescan_failed",
        "goal_closure_blocked",
        "request_goal_gap_plan",
    )):
        return "product_gap"
    return "unknown"


def _detail_failure_class(
    *,
    failure_class: str,
    source: str,
    suggested_route: str,
    recommended_route: str,
    suggested_action: dict[str, Any],
    title: str,
    summary: str,
    fingerprint: str,
) -> str:
    value = str(failure_class or "").strip()
    if value:
        return value
    problem_class = problem_class_for(
        failure_class="",
        source=source,
        suggested_route=suggested_route,
        recommended_route=recommended_route,
        suggested_action=suggested_action,
        title=title,
        summary=summary,
        fingerprint=fingerprint,
    )
    if problem_class == "unknown":
        return "unknown_complex"
    return problem_class


def _default_allowed_actions(problem_class: str) -> list[str]:
    if problem_class in {"workflow_progress", "worker_lifecycle"}:
        return ["diagnose-attention", "workflow-batch-resume", "workflow-task-resume"]
    if problem_class == "runtime_liveness":
        return ["diagnose-attention", "resident-agent-reprompt", "autoresearch"]
    if problem_class == "source_repair":
        return ["autoresearch", "source-repair", "repair-closeout-validate"]
    if problem_class == "external_gate":
        return ["notify-owner", "human-approve", "human-reject"]
    if problem_class in {"artifact_contract", "candidate_quality", "product_gap"}:
        return ["diagnose-attention", "replan", "trigger-rework"]
    return ["diagnose-attention", "autoresearch"]


def _action_allowed_actions(action: dict[str, Any]) -> list[str]:
    values = _string_list(action.get("allowed_actions"))
    name = str(action.get("action") or "").strip()
    safe = str(action.get("safe_resume_action") or "").strip()
    if name:
        values.append(name)
    if safe:
        values.append(safe)
    return values


def _as_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        data = value.to_dict()
    elif hasattr(value, "__dataclass_fields__"):
        data = asdict(value)
    elif isinstance(value, dict):
        data = value
    else:
        data = {}
    return data if isinstance(data, dict) else {}


def _safe_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = []
    return [str(item).strip() for item in raw if str(item).strip()]


def _event_row(event: Any) -> dict[str, Any]:
    if hasattr(event, "to_dict"):
        data = event.to_dict()
    elif hasattr(event, "__dataclass_fields__"):
        data = asdict(event)
    elif isinstance(event, dict):
        data = event
    else:
        data = {}
    return data if isinstance(data, dict) else {}


def _event_scope(row: dict[str, Any], payload: dict[str, Any]) -> str:
    for key in (
        "pdd_id",
        "feature_id",
        "trace_id",
        "target_ref",
        "task_id",
        "fanout_id",
        "dispatch_id",
        "role_instance",
        "worker_id",
        "message_id",
        "attention_id",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    task_id = str(row.get("task_id") or "").strip()
    if task_id:
        return task_id
    actor = str(row.get("actor") or "").strip()
    if actor:
        return actor
    return str(row.get("id") or "unknown")


def _event_policy_fingerprint(
    event_type: str,
    row: dict[str, Any],
    payload: dict[str, Any],
    rule: dict[str, Any],
) -> str:
    fields = _string_list(rule.get("dedupe_key_fields"))
    parts: list[str] = []
    for field in fields:
        value = str(payload.get(field) or row.get(field) or "").strip()
        if value:
            parts.append(f"{field}={value}")
    if parts:
        return f"{event_type}:{':'.join(parts)}"
    return f"{event_type}:{_event_scope(row, payload)}"


def _event_summary(payload: dict[str, Any], *, default: str) -> str:
    for key in (
        "reason",
        "error",
        "summary",
        "message",
        "status",
        "latest_event_type",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return default


def _severity(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"info", "low", "medium", "warn", "high", "critical"}:
        return normalized
    return "medium"


def _problem_id(
    *,
    source: str,
    fingerprint: str,
    failure_class: str,
    problem_class: str,
) -> str:
    raw = "|".join([
        source or "unknown",
        fingerprint or "",
        failure_class or "",
        problem_class or "",
    ])
    return "prb-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "EXPECTED_NEGATIVE_EVENT_TYPES",
    "PROBLEM_CLASSES",
    "PROBLEM_ENVELOPE_SCHEMA_VERSION",
    "abnormal_event_projection",
    "build_problem_envelope",
    "problem_class_for",
    "problem_envelope_from_action",
    "problem_envelope_from_attention",
    "problem_envelope_from_event",
    "problem_envelope_from_failure_signal",
]
