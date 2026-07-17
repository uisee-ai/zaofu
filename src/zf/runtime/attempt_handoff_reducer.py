"""Replay-only projection for implementation attempts and negative handoffs.

The reducer does not emit events or mutate task truth.  It gives recovery,
completion gates, and Web projections one deterministic answer for which
feedback remains open and which handoff is waiting for delivery, a worker
claim, or independent verification.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from zf.core.events.model import ZfEvent


SCHEMA_VERSION = "attempt-handoff-snapshot.v1"
_HANDOFF_OPEN = frozenset({
    "published",
    "acknowledged",
    "resolution_claimed",
    "residual",
})
_VERIFY_SUCCESS = frozenset({
    "verify.passed",
    "test.passed",
    "review.approved",
    "lane.stage.completed",
})
_VERIFY_FAILURE = frozenset({
    "verify.failed",
    "test.failed",
    "review.rejected",
    "lane.stage.failed",
})
_IMPL_SUCCESS = frozenset({
    "dev.build.done",
    "impl.child.completed",
    "fix.child.completed",
    "task.attempt.succeeded",
})


def reduce_attempt_handoffs(
    events: list[ZfEvent],
    *,
    workflow_run_id: str = "",
) -> dict[str, Any]:
    """Derive a replay-stable shadow snapshot from canonical events."""

    if workflow_run_id:
        from zf.runtime.run_scope import events_for_run

        events = events_for_run(events, run_id=workflow_run_id)

    handoffs: dict[str, dict[str, Any]] = {}
    findings: dict[str, dict[str, Any]] = {}
    attempts: dict[str, dict[str, Any]] = {}
    accepted_results: dict[str, dict[str, Any]] = {}
    generations: dict[str, str] = {}
    stale_claims: list[dict[str, str]] = []
    event_by_id: dict[str, ZfEvent] = {}
    seen_ids: set[str] = set()

    for event in events:
        if event.id and event.id in seen_ids:
            continue
        if event.id:
            seen_ids.add(event.id)
            event_by_id[event.id] = event
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = _task_id(event, payload)
        generation = str(payload.get("task_map_generation") or "").strip()
        if task_id and generation:
            generations[task_id] = generation

        if event.type == "task.rework.requested" and task_id:
            finding_ids = _finding_ids(payload, task_id=task_id)
            for prior in handoffs.values():
                if (
                    prior["task_id"] == task_id
                    and prior["status"] in _HANDOFF_OPEN
                    and set(prior["finding_ids"]) & set(finding_ids)
                ):
                    prior["status"] = "superseded"
                    prior["last_event_id"] = event.id
            handoff = {
                "request_event_id": event.id,
                "task_id": task_id,
                "attempt": int(payload.get("attempt") or 0),
                "dispatch_id": str(payload.get("dispatch_id") or ""),
                "delivery_mode": str(payload.get("delivery_mode") or "fresh_session"),
                "workflow_run_id": str(payload.get("workflow_run_id") or event.correlation_id or ""),
                "contract_revision": str(payload.get("contract_revision") or ""),
                "task_map_generation": generation,
                "feedback_id": str(payload.get("feedback_id") or ""),
                "feedback_ref": str(payload.get("rework_feedback_ref") or ""),
                "feedback_digest": str(payload.get("rework_feedback_digest") or ""),
                "finding_ids": finding_ids,
                "status": "published",
                "target_commit": "",
                "evidence_refs": [],
                "last_event_id": event.id,
            }
            handoffs[event.id] = handoff
            for finding_id in finding_ids:
                findings[finding_id] = {
                    "finding_id": finding_id,
                    "feedback_id": handoff["feedback_id"],
                    "task_id": task_id,
                    "request_event_id": event.id,
                    "status": "published",
                    "target_commit": "",
                    "last_event_id": event.id,
                }
            continue

        request_id = _request_id(event, payload, handoffs)
        if event.type in {
            "task.dispatched",
            "task.rework.continuation_injected",
        } and request_id:
            handoff = handoffs[request_id]
            if handoff["status"] == "published":
                handoff["status"] = "acknowledged"
            handoff["dispatch_id"] = str(
                payload.get("dispatch_id") or handoff["dispatch_id"]
            )
            handoff["delivery_mode"] = str(
                payload.get("delivery_mode") or handoff["delivery_mode"]
            )
            handoff["last_event_id"] = event.id
            _set_finding_status(findings, handoff, "acknowledged", event)

        if event.type == "task.dispatched" and task_id:
            attempts[task_id] = {
                "task_id": task_id,
                "dispatch_id": str(payload.get("dispatch_id") or event.id),
                "holder": str(payload.get("assignee") or payload.get("role_instance") or ""),
                "status": "active",
                "source_event_id": event.id,
            }

        if event.type in _IMPL_SUCCESS and task_id:
            handoff = _latest_handoff(handoffs, task_id, {"acknowledged"})
            if handoff is not None:
                target_commit = _target_commit(payload)
                if not target_commit:
                    _record_stale(stale_claims, event, handoff, "missing_target_commit")
                elif not _identity_matches(handoff, payload):
                    _record_stale(stale_claims, event, handoff, "identity_mismatch")
                else:
                    handoff["status"] = "resolution_claimed"
                    handoff["target_commit"] = target_commit
                    handoff["evidence_refs"] = _strings(payload.get("evidence_refs"))
                    handoff["last_event_id"] = event.id
                    _set_finding_status(
                        findings,
                        handoff,
                        "resolution_claimed",
                        event,
                        target_commit=target_commit,
                    )
            attempt = attempts.get(task_id)
            if attempt is not None and _dispatch_matches(attempt, payload):
                attempt["status"] = "reported"
                attempt["result_event_id"] = event.id
                attempt["target_commit"] = _target_commit(payload)

        if event.type == "dispatch.terminal.recorded" and task_id:
            accepted_results[task_id] = {
                "task_id": task_id,
                "dispatch_id": str(payload.get("dispatch_id") or ""),
                "event_type": str(payload.get("event_type") or ""),
                "result_event_id": str(payload.get("event_id") or event.causation_id or ""),
                "record_event_id": event.id,
            }
            attempt = attempts.get(task_id)
            if attempt is not None and _dispatch_matches(attempt, payload):
                attempt["status"] = "accepted"
                attempt["accepted_event_id"] = event.id

        explicit_status = _explicit_feedback_status(event.type)
        if explicit_status and request_id:
            handoff = handoffs[request_id]
            target_commit = _target_commit(payload)
            if explicit_status in {"resolution_claimed", "verified_closed"} and (
                not target_commit or not _identity_matches(handoff, payload)
            ):
                _record_stale(stale_claims, event, handoff, "explicit_identity_mismatch")
            else:
                handoff["status"] = explicit_status
                handoff["target_commit"] = target_commit or handoff["target_commit"]
                handoff["last_event_id"] = event.id
                _set_finding_status(
                    findings,
                    handoff,
                    explicit_status,
                    event,
                    target_commit=handoff["target_commit"],
                )

        if (
            event.type in _VERIFY_SUCCESS
            and task_id
            and _is_independent_verification(event, payload)
            and _verification_passed(event, payload)
        ):
            handoff = _latest_handoff(handoffs, task_id, {"resolution_claimed"})
            if handoff is not None:
                target_commit = _target_commit(payload)
                if not target_commit or target_commit != handoff["target_commit"]:
                    _record_stale(stale_claims, event, handoff, "verification_target_mismatch")
                elif not _identity_matches(handoff, payload):
                    _record_stale(stale_claims, event, handoff, "verification_identity_mismatch")
                else:
                    handoff["status"] = "verified_closed"
                    handoff["last_event_id"] = event.id
                    _set_finding_status(
                        findings,
                        handoff,
                        "verified_closed",
                        event,
                        target_commit=target_commit,
                    )

        if (
            event.type in _VERIFY_FAILURE
            and task_id
            and _is_independent_verification(event, payload)
            and _verification_rejected(event, payload)
        ):
            handoff = _latest_handoff(handoffs, task_id, {"resolution_claimed"})
            if handoff is not None:
                target_commit = _target_commit(payload)
                if target_commit and target_commit == handoff["target_commit"]:
                    handoff["status"] = "residual"
                    handoff["last_event_id"] = event.id
                    _set_finding_status(
                        findings,
                        handoff,
                        "residual",
                        event,
                        target_commit=target_commit,
                    )

    handoff_rows = list(handoffs.values())
    finding_rows = list(findings.values())
    open_findings = [row for row in finding_rows if row["status"] != "verified_closed"]
    pending_handoffs = [row for row in handoff_rows if row["status"] in _HANDOFF_OPEN]
    return {
        "schema_version": SCHEMA_VERSION,
        "is_derived_projection": True,
        "delivery_phase": _delivery_phase(
            events,
            open_findings=open_findings,
            pending_handoffs=pending_handoffs,
            accepted_results=accepted_results,
        ),
        "open_feedback_count": len(open_findings),
        "pending_handoff_count": len(pending_handoffs),
        "open_feedback": open_findings,
        "pending_handoffs": pending_handoffs,
        "handoffs": handoff_rows,
        "active_attempts": list(attempts.values()),
        "accepted_results": list(accepted_results.values()),
        "authoritative_generations": generations,
        "stale_claims": stale_claims,
    }


def _finding_ids(payload: Mapping[str, Any], *, task_id: str) -> list[str]:
    ids = _strings(payload.get("finding_ids"))
    if ids:
        return ids
    seed = "\0".join((
        task_id,
        str(payload.get("failure_fingerprint") or "legacy"),
        str(payload.get("feedback_id") or payload.get("rework_feedback_ref") or ""),
    ))
    return ["finding-legacy-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]]


def _request_id(
    event: ZfEvent,
    payload: Mapping[str, Any],
    handoffs: Mapping[str, Mapping[str, Any]],
) -> str:
    candidates = (
        payload.get("rework_request_event_id"),
        payload.get("task_rework_request_event_id"),
        payload.get("request_event_id"),
        event.causation_id,
    )
    for value in candidates:
        candidate = str(value or "").strip()
        if candidate in handoffs:
            return candidate
    return ""


def _latest_handoff(
    handoffs: Mapping[str, dict[str, Any]],
    task_id: str,
    statuses: set[str],
) -> dict[str, Any] | None:
    for handoff in reversed(list(handoffs.values())):
        if handoff["task_id"] == task_id and handoff["status"] in statuses:
            return handoff
    return None


def _set_finding_status(
    findings: dict[str, dict[str, Any]],
    handoff: Mapping[str, Any],
    status: str,
    event: ZfEvent,
    *,
    target_commit: str = "",
) -> None:
    for finding_id in handoff["finding_ids"]:
        finding = findings.get(finding_id)
        if finding is None or finding["request_event_id"] != handoff["request_event_id"]:
            continue
        finding["status"] = status
        finding["last_event_id"] = event.id
        if target_commit:
            finding["target_commit"] = target_commit


def _record_stale(
    rows: list[dict[str, str]],
    event: ZfEvent,
    handoff: Mapping[str, Any],
    reason: str,
) -> None:
    rows.append({
        "event_id": event.id,
        "event_type": event.type,
        "task_id": str(handoff["task_id"]),
        "request_event_id": str(handoff["request_event_id"]),
        "reason": reason,
    })


def _identity_matches(handoff: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    for key in ("workflow_run_id", "contract_revision", "task_map_generation"):
        expected = str(handoff.get(key) or "").strip()
        actual = str(payload.get(key) or "").strip()
        if expected and actual and expected != actual:
            return False
    expected_dispatch = str(handoff.get("dispatch_id") or "").strip()
    actual_dispatch = str(payload.get("dispatch_id") or "").strip()
    return not (expected_dispatch and actual_dispatch and expected_dispatch != actual_dispatch)


def _dispatch_matches(attempt: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    expected = str(attempt.get("dispatch_id") or "")
    actual = str(payload.get("dispatch_id") or "")
    return not expected or not actual or expected == actual


def _task_id(event: ZfEvent, payload: Mapping[str, Any]) -> str:
    return str(event.task_id or payload.get("task_id") or "").strip()


def _target_commit(payload: Mapping[str, Any]) -> str:
    result = payload.get("verification_result")
    result = result if isinstance(result, Mapping) else {}
    target = payload.get("target_snapshot")
    target = target if isinstance(target, Mapping) else {}
    return str(
        payload.get("target_commit")
        or payload.get("source_commit")
        or payload.get("candidate_head_commit")
        or result.get("target_commit")
        or target.get("target_commit")
        or ""
    ).strip()


def _verification_passed(event: ZfEvent, payload: Mapping[str, Any]) -> bool:
    result = payload.get("verification_result")
    if isinstance(result, Mapping):
        return (
            str(result.get("execution_status") or "completed") == "completed"
            and str(result.get("verdict") or "passed") == "passed"
        )
    return str(payload.get("failure_class") or "none") in {"", "none"}


def _verification_rejected(event: ZfEvent, payload: Mapping[str, Any]) -> bool:
    result = payload.get("verification_result")
    if isinstance(result, Mapping):
        return str(result.get("verdict") or "") == "rejected"
    return str(payload.get("failure_class") or "product_rejection") == "product_rejection"


def _is_independent_verification(
    event: ZfEvent,
    payload: Mapping[str, Any],
) -> bool:
    if event.type.startswith(("verify.", "test.", "review.")):
        return True
    if not event.type.startswith("lane.stage."):
        return False
    owner = " ".join((
        str(payload.get("verification_owner") or ""),
        str(payload.get("verification_tier") or ""),
        str(payload.get("stage_slot") or payload.get("stage_id") or ""),
        str(payload.get("role") or payload.get("role_name") or event.actor or ""),
    )).lower()
    return any(marker in owner for marker in ("verify", "test", "review", "judge"))


def _explicit_feedback_status(event_type: str) -> str:
    return {
        "rework.feedback.published": "published",
        "rework.feedback.acknowledged": "acknowledged",
        "rework.feedback.resolution_claimed": "resolution_claimed",
        "rework.feedback.verified_closed": "verified_closed",
        "rework.feedback.residual": "residual",
        "rework.feedback.rerouted": "rerouted",
    }.get(event_type, "")


def _delivery_phase(
    events: list[ZfEvent],
    *,
    open_findings: list[dict[str, Any]],
    pending_handoffs: list[dict[str, Any]],
    accepted_results: Mapping[str, Mapping[str, Any]],
) -> str:
    if any(event.type in {"ship.completed", "ship.done"} for event in events):
        return "ship_delivered"
    if any(event.type == "run.goal.completed" for event in events):
        return "goal_completed"
    if open_findings:
        statuses = {row["status"] for row in open_findings}
        if "residual" in statuses:
            return "feedback_residual"
        if "resolution_claimed" in statuses:
            return "feedback_resolution_claimed"
        if "acknowledged" in statuses:
            return "feedback_acknowledged"
        return "feedback_published"
    if pending_handoffs:
        return "handoff_pending"
    if accepted_results:
        return "result_accepted"
    if any(event.type in _IMPL_SUCCESS for event in events):
        return "result_reported"
    if any(event.type == "task.dispatched" for event in events):
        return "provider_running"
    return "not_started"


def _strings(value: Any) -> list[str]:
    source = value if isinstance(value, (list, tuple, set)) else []
    return [str(item).strip() for item in source if str(item).strip()]


__all__ = ["SCHEMA_VERSION", "reduce_attempt_handoffs"]
