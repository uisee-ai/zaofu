"""Run Manager-owned semantic triage actions for repeated task failures."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.semantic_replan import (
    SEMANTIC_REPLAN_ACTION,
    SEMANTIC_REPLAN_SAFE_ACTION,
)


TRIAGE_REQUESTED = "orchestrator.rework.triage.requested"
TRIAGE_RECORDED = "orchestrator.rework.triage.recorded"
TRIAGE_REQUEST_ACTION = "orchestrator-rework-triage"
TRIAGE_APPLY_ACTION = "orchestrator-triage-advice-apply"
_TRIAGE_CAP_EVENTS = frozenset({"task.rework.capped", "candidate.rework.capped"})
ORCHESTRATOR_TRIAGE_ACTIONS = frozenset({
    TRIAGE_REQUEST_ACTION,
    TRIAGE_APPLY_ACTION,
    SEMANTIC_REPLAN_ACTION,
})

_PROGRESS_EVENTS = frozenset({
    "task.dispatched",
    "task.rework.requested",
    "dev.build.done",
    "impl.child.completed",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
    "task.done",
    "orchestrator.replan_requested",
    "task_map.ready",
    "candidate.ready",
    "run.goal.completed",
})
_REWORK_RECOMMENDATIONS = frozenset({
    "continue_rework",
    "precise_rework",
})
_REPLAN_RECOMMENDATIONS = frozenset({
    "revise_contract",
    "split_task",
    "replan",
})
_DIAGNOSIS_RECOMMENDATIONS = frozenset({"diagnose", "autoresearch"})
_HUMAN_RECOMMENDATIONS = frozenset({"human", "escalate_human"})


def pending_rework_triage_actions(
    events: list[ZfEvent],
    *,
    threshold: int,
    stale_seconds: int,
    advisor_available: bool = True,
    resident_advisor: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Derive one current Run Manager action per repeated-failure scope."""

    if threshold <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    groups: dict[tuple[str, str], tuple[int, ZfEvent]] = {}
    for index, event in enumerate(events):
        if event.type not in _TRIAGE_CAP_EVENTS:
            continue
        payload = _payload(event)
        if not is_semantic_triage_cap(event, threshold=threshold):
            continue
        task_id = str(
            event.task_id
            or payload.get("task_id")
            or payload.get("pdd_id")
            or ""
        )
        fingerprint = str(
            payload.get("failure_fingerprint")
            or payload.get("fingerprint")
            or event.id
        )
        failure_count = int(payload.get("failure_count") or payload.get("retry_count") or 0)
        if not task_id or failure_count < threshold:
            continue
        groups[(task_id, fingerprint)] = (index, event)

    out: list[dict[str, Any]] = []
    for (task_id, fingerprint), (capped_index, capped) in groups.items():
        if _has_later_task_progress(events, capped_index, task_id):
            continue
        request_id = _triage_request_id(capped, task_id, fingerprint)
        request = _matching_request(events, request_id)
        recorded = _matching_recorded(events, request_id)
        if recorded is not None:
            action = _recorded_advice_action(recorded, capped, request_id, fingerprint)
        elif request is not None:
            if _event_age_seconds(request, now) < stale_seconds:
                continue
            action = _timeout_fallback_action(request, capped, request_id, fingerprint)
        elif not advisor_available:
            resident_attempt = _resident_advisor_attempt(events, request_id)
            if resident_attempt is not None:
                if _event_age_seconds(resident_attempt, now) < stale_seconds:
                    continue
                action = _resident_timeout_fallback_action(
                    resident_attempt,
                    capped,
                    request_id,
                    fingerprint,
                )
            else:
                action = _unavailable_fallback_action(
                    capped,
                    request_id,
                    fingerprint,
                    resident_advisor=resident_advisor,
                )
        else:
            action = _request_action(capped, request_id, fingerprint, threshold)
        if not _action_completed(events, str(action.get("checkpoint_id") or "")):
            out.append(action)
    return out


def active_rework_triage_task_ids(
    events: list[ZfEvent],
    *,
    threshold: int,
) -> set[str]:
    """Return task scopes whose old mechanical recovery must stay suppressed."""

    active: set[str] = set()
    if threshold <= 0:
        return active
    for index, event in enumerate(events):
        if event.type != "task.rework.capped":
            continue
        payload = _payload(event)
        if not is_semantic_triage_cap(event, threshold=threshold):
            continue
        task_id = str(event.task_id or payload.get("task_id") or "")
        failure_count = int(payload.get("failure_count") or payload.get("retry_count") or 0)
        if (
            task_id
            and failure_count >= threshold
            and not _has_later_task_progress(events, index, task_id)
        ):
            active.add(task_id)
    return active


def _request_action(
    capped: ZfEvent,
    request_id: str,
    fingerprint: str,
    threshold: int,
) -> dict[str, Any]:
    payload = _payload(capped)
    evidence_ids = _string_list(payload.get("failure_event_ids")) or [
        str(payload.get("trigger_event_id") or capped.id)
    ]
    return {
        "schema_version": "run-manager.pending-action.v1",
        "action": TRIAGE_REQUEST_ACTION,
        "checkpoint_id": _stable_id("ortriage-request", request_id),
        "safe_resume_action": "request_orchestrator_triage",
        "request_id": request_id,
        "task_id": str(
            capped.task_id
            or payload.get("task_id")
            or payload.get("pdd_id")
            or ""
        ),
        "role": str(payload.get("role") or ""),
        "fingerprint": fingerprint,
        "failure_class": (
            "candidate_rework_exhausted"
            if str(payload.get("failure_scope") or "") == "candidate"
            else "repeated_task_failure"
        ),
        "failure_count": int(payload.get("failure_count") or payload.get("retry_count") or 0),
        "triage_threshold": threshold,
        "owner_route": "run_manager",
        "action_policy": "auto_decide",
        "intervention_class": "semantic_replan",
        "source_event_id": capped.id,
        "source_event_type": capped.type,
        "source_event_ids": evidence_ids,
        "failure_event_ids": evidence_ids,
        "trigger_event_type": str(payload.get("trigger_event_type") or ""),
        "summary": str(payload.get("last_reason") or "rework threshold reached"),
        "expected_downstream_events": [TRIAGE_REQUESTED],
        "verify_condition": f"expected_downstream_event:{TRIAGE_REQUESTED}",
        **_candidate_scope_fields(payload),
    }


def _recorded_advice_action(
    recorded: ZfEvent,
    capped: ZfEvent,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any]:
    payload = _payload(recorded)
    recommendation = str(payload.get("recommended_action") or "").strip().lower()
    capped_payload = _payload(capped)
    if str(capped_payload.get("failure_scope") or "") == "candidate":
        return _recorded_candidate_advice_action(
            recorded=recorded,
            capped=capped,
            request_id=request_id,
            fingerprint=fingerprint,
            recommendation=recommendation,
        )
    base = {
        "schema_version": "run-manager.pending-action.v1",
        "checkpoint_id": _stable_id("ortriage-advice", request_id, recorded.id),
        "request_id": request_id,
        "task_id": str(recorded.task_id or capped.task_id or payload.get("task_id") or ""),
        "role": str(_payload(capped).get("role") or ""),
        "fingerprint": fingerprint,
        "failure_class": "repeated_task_failure",
        "owner_route": "run_manager",
        "source_event_id": recorded.id,
        "source_event_type": recorded.type,
        "source_event_ids": [
            capped.id,
            *_string_list(_payload(capped).get("failure_event_ids")),
            recorded.id,
        ],
        "failure_event_ids": _string_list(_payload(capped).get("failure_event_ids")),
        "failure_count": int(
            _payload(capped).get("failure_count")
            or _payload(capped).get("retry_count")
            or 0
        ),
        "recorded_event_id": recorded.id,
        "recommended_action": recommendation,
        "guidance": str(payload.get("guidance") or payload.get("reason") or ""),
        "advice": payload,
    }
    if recommendation in _REPLAN_RECOMMENDATIONS:
        return {
            **base,
            "action": SEMANTIC_REPLAN_ACTION,
            "safe_resume_action": SEMANTIC_REPLAN_SAFE_ACTION,
            "failure_class": "semantic_replan_artifact_required",
            "action_policy": "auto_decide",
            "intervention_class": "semantic_replan",
            "expected_downstream_events": [],
            "verify_condition": "",
        }
    if recommendation in _DIAGNOSIS_RECOMMENDATIONS:
        return {
            **base,
            "failure_class": "repeated_task_failure",
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose",
            "expected_downstream_events": [
                "run.manager.autoresearch.requested",
                "run.manager.resident.prompted",
            ],
            "verify_condition": (
                "expected_downstream_event:run.manager.autoresearch.requested,"
                "run.manager.resident.prompted"
            ),
        }
    if recommendation in _HUMAN_RECOMMENDATIONS or recommendation not in _REWORK_RECOMMENDATIONS:
        return {
            **base,
            "action": TRIAGE_APPLY_ACTION,
            "safe_resume_action": "apply_orchestrator_triage_advice",
            "action_policy": "human_escalate",
            "intervention_class": "human_decision",
            "expected_downstream_events": ["human.escalate"],
            "verify_condition": "expected_downstream_event:human.escalate",
        }
    return {
        **base,
        "action": TRIAGE_APPLY_ACTION,
        "safe_resume_action": "apply_orchestrator_triage_advice",
        "action_policy": "auto_decide",
        "intervention_class": "semantic_replan",
        "expected_downstream_events": ["task.rework.requested", "task.assigned"],
        "verify_condition": "expected_downstream_event:task.rework.requested,task.assigned",
    }


def _recorded_candidate_advice_action(
    *,
    recorded: ZfEvent,
    capped: ZfEvent,
    request_id: str,
    fingerprint: str,
    recommendation: str,
) -> dict[str, Any]:
    capped_payload = _payload(capped)
    advice = _payload(recorded)
    context = capped_payload.get("candidate_rework_context")
    context = dict(context) if isinstance(context, dict) else {}
    guidance = str(advice.get("guidance") or advice.get("reason") or "").strip()
    feedback = _string_list(context.get("rework_feedback"))
    if guidance and guidance not in feedback:
        feedback.append(guidance)
    failed_task_ids = _string_list(context.get("failed_task_ids"))

    if recommendation in _DIAGNOSIS_RECOMMENDATIONS:
        return {
            "schema_version": "run-manager.pending-action.v1",
            "action": "diagnose-attention",
            "checkpoint_id": _stable_id("ortriage-candidate-diagnose", request_id, recorded.id),
            "safe_resume_action": "diagnose_attention",
            "request_id": request_id,
            "task_id": str(capped.task_id or capped_payload.get("pdd_id") or ""),
            "pdd_id": str(capped_payload.get("pdd_id") or ""),
            "trace_id": str(capped_payload.get("trace_id") or ""),
            "fingerprint": fingerprint,
            "failure_class": "candidate_rework_exhausted",
            "owner_route": "run_manager",
            "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose",
            "source_event_id": recorded.id,
            "source_event_type": recorded.type,
            "source_event_ids": [capped.id, recorded.id],
            "failure_event_ids": _string_list(capped_payload.get("failure_event_ids")),
            "recorded_event_id": recorded.id,
            "recommended_action": recommendation,
            "guidance": guidance,
            "summary": guidance or "candidate recovery cap requires diagnosis",
            "expected_downstream_events": [
                "run.manager.autoresearch.requested",
                "run.manager.resident.prompted",
            ],
            "verify_condition": (
                "expected_downstream_event:run.manager.autoresearch.requested,"
                "run.manager.resident.prompted"
            ),
        }

    if recommendation in _HUMAN_RECOMMENDATIONS or recommendation not in (
        _REWORK_RECOMMENDATIONS | _REPLAN_RECOMMENDATIONS
    ):
        candidate_action = "escalate"
        expected = ["human.escalate", "owner.visible_message.requested"]
        intervention = "human_decision"
        owner_route = "human_escalation"
    elif recommendation in _REWORK_RECOMMENDATIONS and failed_task_ids:
        candidate_action = "retrigger"
        expected = ["task_map.ready"]
        intervention = "semantic_replan"
        owner_route = "controlled_action"
    else:
        candidate_action = "replan"
        expected = ["orchestrator.replan_requested"]
        intervention = "semantic_replan"
        owner_route = "orchestrator_replan"

    return {
        "schema_version": "run-manager.pending-action.v1",
        **context,
        "action": "candidate-rework-apply",
        "checkpoint_id": _stable_id("ortriage-candidate-advice", request_id, recorded.id),
        "safe_resume_action": f"candidate_{candidate_action}",
        "candidate_rework_action": candidate_action,
        "candidate_retry_mode": "",
        "orchestrator_triage_applied": True,
        "semantic_triage_pending": False,
        "request_id": request_id,
        "task_id": str(capped.task_id or capped_payload.get("pdd_id") or ""),
        "pdd_id": str(context.get("pdd_id") or capped_payload.get("pdd_id") or ""),
        "trace_id": str(context.get("trace_id") or capped_payload.get("trace_id") or ""),
        "fingerprint": fingerprint,
        "failure_fingerprint": fingerprint,
        "failure_class": f"candidate_rework_{candidate_action}",
        "owner_route": owner_route,
        "action_policy": "auto_decide",
        "intervention_class": intervention,
        "source_event_id": str(context.get("source_event_id") or capped.id),
        "source_event_type": str(context.get("source_event_type") or capped.type),
        "source_event_ids": [
            *_string_list(capped_payload.get("failure_event_ids")),
            capped.id,
            recorded.id,
        ],
        "failure_event_ids": _string_list(capped_payload.get("failure_event_ids")),
        "failed_task_ids": failed_task_ids,
        "rework_feedback": feedback,
        "rework_attempt": int(
            context.get("rework_attempt")
            or capped_payload.get("failure_count")
            or 0
        ),
        "recorded_event_id": recorded.id,
        "recommended_action": recommendation,
        "guidance": guidance,
        "advice": advice,
        "expected_downstream_events": expected,
        "verify_condition": "expected_downstream_event:" + ",".join(expected),
        "route_registry": "run-manager-router.v1",
    }


def _candidate_scope_fields(payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("failure_scope") or "") != "candidate":
        return {}
    return {
        "recovery_scope": "candidate",
        "pdd_id": str(payload.get("pdd_id") or payload.get("task_id") or ""),
        "trace_id": str(payload.get("trace_id") or ""),
        "candidate_rework_context": payload.get("candidate_rework_context")
        if isinstance(payload.get("candidate_rework_context"), dict) else {},
    }


def _timeout_fallback_action(
    request: ZfEvent,
    capped: ZfEvent,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema_version": "run-manager.pending-action.v1",
        "action": "diagnose-attention",
        "checkpoint_id": _stable_id("ortriage-timeout", request_id),
        "safe_resume_action": "diagnose_attention",
        "request_id": request_id,
        "task_id": str(request.task_id or capped.task_id or ""),
        "fingerprint": fingerprint,
        "failure_class": "orchestrator_triage_timeout",
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
        "source_event_id": request.id,
        "source_event_type": request.type,
        "source_event_ids": [capped.id, request.id],
        "summary": "Orchestrator semantic triage did not answer within the bounded window",
        "expected_downstream_events": [
            "run.manager.autoresearch.requested",
            "run.manager.resident.prompted",
        ],
        "verify_condition": (
            "expected_downstream_event:run.manager.autoresearch.requested,"
            "run.manager.resident.prompted"
        ),
    }


def _unavailable_fallback_action(
    capped: ZfEvent,
    request_id: str,
    fingerprint: str,
    *,
    resident_advisor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _payload(capped)
    if _resident_advisor_available(resident_advisor):
        resident_advisor = resident_advisor or {}
        return {
            "schema_version": "run-manager.pending-action.v1",
            "action": "resident-agent-reprompt",
            "checkpoint_id": _stable_id("ortriage-resident", request_id),
            "safe_resume_action": "resident_agent_reprompt",
            "request_id": request_id,
            "semantic_triage_request_id": request_id,
            "task_id": str(capped.task_id or payload.get("task_id") or ""),
            "role": str(payload.get("role") or ""),
            "fingerprint": fingerprint,
            "failure_class": "repeated_task_failure",
            "failure_count": int(payload.get("failure_count") or payload.get("retry_count") or 0),
            "failure_event_ids": _string_list(payload.get("failure_event_ids")),
            "owner_route": "run_manager",
            "action_policy": "auto_decide",
            "intervention_class": "semantic_replan",
            "source_event_id": capped.id,
            "source_event_type": capped.type,
            "source_event_ids": [capped.id, *_string_list(payload.get("failure_event_ids"))],
            "summary": "No Orchestrator Agent is configured; resident Run Manager must classify the repeated failure",
            "recommended_actions": [
                "continue_rework",
                "precise_rework",
                "revise_contract",
                "split_task",
                "replan",
                "diagnose",
                "human",
            ],
            "expected_output": ["orchestrator.rework.triage.recorded"],
            "expected_downstream_events": ["run.manager.resident.prompted"],
            "verify_condition": "expected_downstream_event:run.manager.resident.prompted",
            "tmux_session": str(resident_advisor.get("tmux_session") or ""),
            "session_mode": str(resident_advisor.get("session_mode") or ""),
            "briefing_path": str(resident_advisor.get("briefing_path") or ""),
            "instance_id": str(resident_advisor.get("instance_id") or "run-manager"),
            "role_instance": str(resident_advisor.get("instance_id") or "run-manager"),
            "diagnosis_source_action": "orchestrator-rework-triage",
            "suggested_route": "run_manager_resident_agent",
        }
    return {
        "schema_version": "run-manager.pending-action.v1",
        "action": "diagnose-attention",
        "checkpoint_id": _stable_id("ortriage-unavailable", request_id),
        "safe_resume_action": "diagnose_attention",
        "request_id": request_id,
        "task_id": str(capped.task_id or payload.get("task_id") or ""),
        "fingerprint": fingerprint,
        "failure_class": "orchestrator_triage_unavailable",
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
        "source_event_id": capped.id,
        "source_event_type": capped.type,
        "source_event_ids": [capped.id],
        "summary": "No Orchestrator Agent is configured; diagnose repeated failure now",
        "expected_downstream_events": [
            "run.manager.autoresearch.requested",
            "run.manager.resident.prompted",
        ],
        "verify_condition": (
            "expected_downstream_event:run.manager.autoresearch.requested,"
            "run.manager.resident.prompted"
        ),
    }


def _resident_advisor_attempt(
    events: list[ZfEvent],
    request_id: str,
) -> ZfEvent | None:
    checkpoint_id = _stable_id("ortriage-resident", request_id)
    for event in reversed(events):
        if event.type not in {
            "run.manager.action.applied",
            "run.manager.action.verify.passed",
        }:
            continue
        if str(_payload(event).get("checkpoint_id") or "") == checkpoint_id:
            return event
    return None


def _resident_timeout_fallback_action(
    attempt: ZfEvent,
    capped: ZfEvent,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any]:
    payload = _payload(capped)
    return {
        "schema_version": "run-manager.pending-action.v1",
        "action": "diagnose-attention",
        "checkpoint_id": _stable_id("ortriage-resident-timeout", request_id),
        "safe_resume_action": "diagnose_attention",
        "request_id": request_id,
        "task_id": str(capped.task_id or payload.get("task_id") or ""),
        "fingerprint": fingerprint,
        "failure_class": "resident_orchestrator_triage_timeout",
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
        "source_event_id": attempt.id,
        "source_event_type": attempt.type,
        "source_event_ids": [
            capped.id,
            *_string_list(payload.get("failure_event_ids")),
            attempt.id,
        ],
        "failure_event_ids": _string_list(payload.get("failure_event_ids")),
        "summary": "Resident semantic triage did not answer within the bounded window",
        "expected_downstream_events": ["run.manager.autoresearch.requested"],
        "verify_condition": "expected_downstream_event:run.manager.autoresearch.requested",
    }


def _resident_advisor_available(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("status") or "") in {"", "disabled", "not_spawned"}:
        return False
    return bool(
        str(value.get("tmux_session") or "").strip()
        and str(value.get("briefing_path") or "").strip()
        and str(value.get("instance_id") or "run-manager").strip()
    )


def _matching_request(events: list[ZfEvent], request_id: str) -> ZfEvent | None:
    return next((
        event for event in reversed(events)
        if event.type == TRIAGE_REQUESTED
        and str(_payload(event).get("request_id") or "") == request_id
    ), None)


def _matching_recorded(
    events: list[ZfEvent],
    request_id: str,
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type != TRIAGE_RECORDED:
            continue
        payload = _payload(event)
        if str(payload.get("request_id") or "") == request_id:
            return event
    return None


def _triage_request_id(capped: ZfEvent, task_id: str, fingerprint: str) -> str:
    payload = _payload(capped)
    evidence_ids = sorted(_string_list(payload.get("failure_event_ids")))
    evidence_key = "|".join(evidence_ids) or str(
        payload.get("trigger_event_id") or capped.id
    )
    return _stable_id("ortriage", task_id, fingerprint, evidence_key)


def _has_later_task_progress(events: list[ZfEvent], start: int, task_id: str) -> bool:
    for event in events[start + 1:]:
        payload = _payload(event)
        event_task = str(
            event.task_id
            or payload.get("task_id")
            or payload.get("pdd_id")
            or payload.get("feature_id")
            or ""
        )
        if event_task == task_id and event.type in _PROGRESS_EVENTS:
            return True
    return False


def _action_completed(events: list[ZfEvent], checkpoint_id: str) -> bool:
    if not checkpoint_id:
        return False
    for event in events:
        if event.type not in {
            "run.manager.action.applied",
            "run.manager.action.blocked",
            "run.manager.action.failed",
        }:
            continue
        if str(_payload(event).get("checkpoint_id") or "") == checkpoint_id:
            return True
    return False


def _event_age_seconds(event: ZfEvent, now: datetime) -> int:
    try:
        timestamp = datetime.fromisoformat(str(event.ts).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0, int((now - timestamp).total_seconds()))
    except Exception:
        return 0


def is_semantic_triage_cap(event: ZfEvent, *, threshold: int) -> bool:
    """Separate same-fingerprint semantic caps from aggregate retry exhaustion."""

    if event.type not in _TRIAGE_CAP_EVENTS or threshold <= 0:
        return False
    payload = _payload(event)
    if bool(payload.get("semantic_triage_required")):
        return True
    evidence_ids = _string_list(payload.get("failure_event_ids"))
    return bool(
        str(payload.get("failure_fingerprint") or "").strip()
        and int(payload.get("failure_count") or 0) >= threshold
        and len(set(evidence_ids)) >= threshold
    )


def triage_action_preflight(
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate the evidence required by Run Manager semantic actions."""

    failures: list[str] = []
    checkpoint_id = str(payload.get("checkpoint_id") or "")
    safe_action = str(payload.get("safe_resume_action") or "")
    if not checkpoint_id:
        failures.append("missing_checkpoint_id")
    if not str(payload.get("request_id") or ""):
        failures.append("missing_request_id")
    if not str(payload.get("task_id") or ""):
        failures.append("missing_task_id")
    if not str(payload.get("fingerprint") or ""):
        failures.append("missing_failure_fingerprint")
    if action == TRIAGE_REQUEST_ACTION:
        threshold = int(payload.get("triage_threshold") or 3)
        if int(payload.get("failure_count") or 0) < threshold:
            failures.append("failure_count_below_semantic_triage_threshold")
        if not _string_list(payload.get("source_event_ids")):
            failures.append("missing_failure_evidence")
    elif action == SEMANTIC_REPLAN_ACTION:
        if not str(payload.get("recorded_event_id") or ""):
            failures.append("missing_recorded_event_id")
        if not str(payload.get("semantic_replan_trigger") or ""):
            failures.append("missing_semantic_replan_trigger")
        if not str(payload.get("task_map_ref") or ""):
            failures.append("missing_task_map_ref")
        if not str(payload.get("pdd_id") or payload.get("feature_id") or ""):
            failures.append("missing_pdd_id")
    else:
        if not str(payload.get("recorded_event_id") or ""):
            failures.append("missing_recorded_event_id")
        if (
            str(payload.get("recommended_action") or "")
            in {"continue_rework", "precise_rework"}
            and not str(payload.get("role") or "")
        ):
            failures.append("missing_rework_target_role")
    expected = sorted(
        _string_list(payload.get("expected_downstream_events"))
        or triage_expected_downstream_events(safe_action)
    )
    return {
        "schema_version": "run-manager.action-preflight.v1",
        "status": "blocked" if failures else "passed",
        "failures": failures,
        "warnings": [],
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": safe_action,
        "expected_downstream_events": expected,
        "verify_condition": str(payload.get("verify_condition") or "")
        or "expected_downstream_event:" + ",".join(expected),
    }


def triage_expected_downstream_events(safe_action: str) -> set[str]:
    if safe_action == "request_orchestrator_triage":
        return {TRIAGE_REQUESTED}
    if safe_action == "apply_orchestrator_triage_advice":
        return {"task.rework.requested", "task.assigned"}
    if safe_action == SEMANTIC_REPLAN_SAFE_ACTION:
        return {"flow.discovery.requested", "verify.parity_scan.requested"}
    return set()


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value or [] if str(item).strip()]


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "ORCHESTRATOR_TRIAGE_ACTIONS",
    "TRIAGE_APPLY_ACTION",
    "TRIAGE_RECORDED",
    "TRIAGE_REQUEST_ACTION",
    "TRIAGE_REQUESTED",
    "active_rework_triage_task_ids",
    "is_semantic_triage_cap",
    "pending_rework_triage_actions",
    "triage_action_preflight",
    "triage_expected_downstream_events",
]
