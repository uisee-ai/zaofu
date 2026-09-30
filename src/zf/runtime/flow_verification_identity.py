"""Identity matching for verification resumed by candidate materialization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import same_task_map_generation


def latest_flow_verification_for_candidate(
    events: Sequence[ZfEvent],
    *,
    candidate_event: ZfEvent,
) -> ZfEvent | None:
    """Return one unconsumed verification bound to the candidate identity."""

    payload = _payload(candidate_event)
    workflow_run_id = _text(payload, "workflow_run_id", "run_id")
    goal_id = _text(payload, "pdd_id", "feature_id")
    task_map_generation = _text(payload, "task_map_generation")
    task_map_ref = _text(payload, "task_map_ref")
    candidate_ref = _text(payload, "candidate_ref", "target_ref")
    candidate_head = _text(payload, "candidate_head_commit", "target_commit")

    for verification in reversed(events):
        if verification.type not in {"verify.passed", "test.passed"}:
            continue
        if verification.task_id:
            continue
        body = _payload(verification)
        if not _same_run_or_goal(
            body,
            workflow_run_id=workflow_run_id,
            goal_id=goal_id,
        ):
            continue
        if _text(body, "status") not in {"", "completed", "passed"}:
            continue
        if not _same_task_map(
            body,
            task_map_generation=task_map_generation,
            task_map_ref=task_map_ref,
        ):
            continue
        verification_ref = _text(body, "candidate_ref", "target_ref")
        if candidate_ref and verification_ref and candidate_ref != verification_ref:
            continue
        verification_head = _text(body, "candidate_head_commit", "target_commit")
        if candidate_head and verification_head and candidate_head != verification_head:
            continue
        if _verification_was_consumed(events, verification.id):
            continue
        return verification
    return None


def _same_run_or_goal(
    payload: Mapping[str, object],
    *,
    workflow_run_id: str,
    goal_id: str,
) -> bool:
    verification_run_id = _text(payload, "workflow_run_id", "run_id")
    verification_goal_id = _text(payload, "pdd_id", "feature_id")
    if workflow_run_id:
        return verification_run_id == workflow_run_id
    return not goal_id or verification_goal_id == goal_id


def _same_task_map(
    payload: Mapping[str, object],
    *,
    task_map_generation: str,
    task_map_ref: str,
) -> bool:
    verification_generation = _text(payload, "task_map_generation")
    if task_map_generation and verification_generation:
        return same_task_map_generation(
            task_map_generation,
            verification_generation,
        )
    verification_ref = _text(payload, "task_map_ref")
    return bool(task_map_ref and verification_ref and task_map_ref == verification_ref)


def _verification_was_consumed(
    events: Sequence[ZfEvent],
    verification_event_id: str,
) -> bool:
    return any(
        event.type in {"flow.discovery.requested", "flow.goal.closed"}
        and (
            _text(_payload(event), "verification_event_id") == verification_event_id
            or _text(_payload(event), "source_event_id") == verification_event_id
        )
        for event in events
    )


def _payload(event: ZfEvent) -> Mapping[str, object]:
    return event.payload if isinstance(event.payload, dict) else {}


def _text(payload: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


__all__ = ["latest_flow_verification_for_candidate"]
