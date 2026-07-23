"""Mechanical binding between a canonical Goal claim and Candidate Verify."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import same_task_map_generation


GOAL_COMPLETION_REVERIFY_CAP = 2


@dataclass(frozen=True)
class GoalVerificationBinding:
    status: str
    invalid_reason: str
    verified_target_commit: str = ""
    verification_event_id: str = ""
    verification_admitted_ref: dict[str, Any] = field(default_factory=dict)
    candidate_event_id: str = ""
    candidate_payload: dict[str, Any] = field(default_factory=dict)


def bind_canonical_goal_verification(
    events: Sequence[ZfEvent],
    *,
    claim_payload: Mapping[str, Any],
) -> GoalVerificationBinding:
    """Select only the admitted Verify for this exact Candidate generation."""

    target_commit = str(claim_payload.get("target_commit") or "").strip()
    candidate_ref = str(claim_payload.get("candidate_ref") or "").strip()
    generation = str(claim_payload.get("task_map_generation") or "").strip()
    candidate_event = _current_candidate(
        events,
        candidate_ref=candidate_ref,
        target_commit=target_commit,
    )
    if candidate_event is None:
        return GoalVerificationBinding(
            status="missing",
            invalid_reason="candidate_identity_missing",
        )

    mismatch: tuple[ZfEvent, str, dict[str, Any]] | None = None
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        admitted_ref = _admitted_verification_ref(event, payload)
        if not admitted_ref:
            continue
        if not same_task_map_generation(
            str(payload.get("task_map_generation") or ""),
            generation,
        ):
            continue
        verify_candidate_ref = str(
            payload.get("candidate_ref") or payload.get("target_ref") or ""
        ).strip()
        if not verify_candidate_ref or verify_candidate_ref != candidate_ref:
            continue
        verified_target = str(
            payload.get("target_commit")
            or payload.get("candidate_head_commit")
            or ""
        ).strip()
        if not verified_target:
            continue
        if verified_target == target_commit:
            return GoalVerificationBinding(
                status="exact",
                invalid_reason="",
                verified_target_commit=verified_target,
                verification_event_id=event.id,
                verification_admitted_ref=admitted_ref,
                candidate_event_id=candidate_event.id,
                candidate_payload=dict(candidate_event.payload or {}),
            )
        if mismatch is None:
            mismatch = (event, verified_target, admitted_ref)

    if mismatch is not None:
        event, verified_target, admitted_ref = mismatch
        return GoalVerificationBinding(
            status="mismatch",
            invalid_reason="verification_target_mismatch",
            verified_target_commit=verified_target,
            verification_event_id=event.id,
            verification_admitted_ref=admitted_ref,
            candidate_event_id=candidate_event.id,
            candidate_payload=dict(candidate_event.payload or {}),
        )
    return GoalVerificationBinding(
        status="missing",
        invalid_reason="verification_evidence_missing",
        candidate_event_id=candidate_event.id,
        candidate_payload=dict(candidate_event.payload or {}),
    )


def _current_candidate(
    events: Sequence[ZfEvent],
    *,
    candidate_ref: str,
    target_commit: str,
) -> ZfEvent | None:
    if not candidate_ref or not target_commit:
        return None
    for event in reversed(events):
        if event.type != "candidate.ready" or not isinstance(event.payload, dict):
            continue
        payload = event.payload
        if str(payload.get("candidate_ref") or "").strip() != candidate_ref:
            continue
        if str(payload.get("candidate_head_commit") or "").strip() != target_commit:
            continue
        return event
    return None


def _admitted_verification_ref(
    event: ZfEvent,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if event.type != "fanout.child.completed":
        return {}
    admitted_ref = payload.get("admitted_call_result_ref")
    if (
        str(payload.get("control_result_schema") or "")
        != "verification-result.v1"
        or str(payload.get("semantic_verdict") or "").lower() != "passed"
        or not isinstance(admitted_ref, Mapping)
        or not str(admitted_ref.get("ref") or "").strip()
        or not str(admitted_ref.get("sha256") or "").strip()
    ):
        return {}
    return dict(admitted_ref)


__all__ = [
    "GOAL_COMPLETION_REVERIFY_CAP",
    "GoalVerificationBinding",
    "bind_canonical_goal_verification",
]
