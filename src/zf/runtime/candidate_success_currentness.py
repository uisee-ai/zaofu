"""Consumption-time currentness checks for candidate success events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


_CANDIDATE_SUCCESS_EVENTS = frozenset({
    "candidate.quality.passed",
    "judge.passed",
    "test.passed",
    "verify.passed",
})
_REWORK_AUTHORITY_EVENTS = frozenset({
    "candidate.rework.requested",
    "orchestrator.replan_requested",
    "task.rework.requested",
})


@dataclass(frozen=True)
class CandidateSuccessCurrentness:
    applies: bool
    current: bool
    issues: tuple[dict[str, str], ...] = ()
    superseded_by: str = ""


class CandidateSuccessCurrentnessMixin:
    """Emit one audit verdict and block stale Candidate success consumption."""

    def _reject_stale_candidate_success(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision | None:
        verdict = evaluate_candidate_success_currentness(
            self.event_log.read_all(),
            event,
        )
        if not verdict.applies or verdict.current:
            return None
        existing = next((
            candidate
            for candidate in reversed(self.event_log.read_all())
            if candidate.type == "candidate.result.superseded"
            and candidate.causation_id == event.id
        ), None)
        if existing is None:
            payload = event.payload if isinstance(event.payload, dict) else {}
            self.event_writer.append(ZfEvent(
                type="candidate.result.superseded",
                actor="zf-cli",
                payload={
                    "schema_version": "candidate-result-currentness.v1",
                    "source_event_id": event.id,
                    "source_event_type": event.type,
                    "workflow_run_id": str(payload.get("workflow_run_id") or ""),
                    "issues": [dict(item) for item in verdict.issues],
                    "superseded_by": verdict.superseded_by,
                    "semantic_attempt_incremented": False,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        return WorkflowRuntimeDecision(
            action="supersede",
            task_id=str(event.task_id or ""),
            reason=f"{event.type} is stale at candidate-success consumption",
        )


def evaluate_candidate_success_currentness(
    events: Sequence[ZfEvent],
    event: ZfEvent,
) -> CandidateSuccessCurrentness:
    """Recheck an admitted candidate success immediately before consumption.

    Admission proves that a result matched the authority visible at submit
    time.  This check closes the later race where rework or a replacement
    generation becomes authoritative before discovery, Judge, or terminal
    consumes that already-admitted success.
    """

    payload = event.payload if isinstance(event.payload, Mapping) else {}
    if not _is_candidate_success(event, payload):
        return CandidateSuccessCurrentness(applies=False, current=True)

    workflow_run_id = _text(
        payload.get("workflow_run_id")
        or payload.get("trace_id")
        or event.correlation_id
    )
    issues: list[dict[str, str]] = []
    if not workflow_run_id:
        issues.append(_issue(
            "workflow_run_id",
            "candidate_currentness_unprovable",
            "candidate success has no workflow_run_id",
        ))

    try:
        current_candidate = _latest_frozen_candidate(events, workflow_run_id)
    except ValueError as exc:
        return CandidateSuccessCurrentness(applies=True, current=False,
            issues=(_issue("candidate_snapshot_event_id", "candidate_currentness_unprovable", str(exc)),))
    if current_candidate is None:
        issues.append(_issue(
            "candidate_snapshot_event_id",
            "candidate_currentness_unprovable",
            "current frozen candidate authority is missing",
        ))
    else:
        current = (
            current_candidate.payload
            if isinstance(current_candidate.payload, Mapping)
            else {}
        )
        _compare_identity(
            issues,
            field="task_map_generation",
            actual=_text(payload.get("task_map_generation")),
            expected=_text(current.get("task_map_generation")),
            code="stale_task_map_generation",
            generation=True,
        )
        _compare_identity(
            issues,
            field="plan_artifact_package_digest",
            actual=_text(payload.get("plan_artifact_package_digest")),
            expected=_text(current.get("plan_artifact_package_digest")),
            code="stale_plan_artifact_package",
        )
        _compare_identity(
            issues,
            field="candidate_ref",
            actual=_text(payload.get("candidate_ref") or payload.get("target_ref")),
            expected=_text(current.get("candidate_ref")),
            code="stale_candidate_ref",
        )
        _compare_identity(
            issues,
            field="candidate_head_commit",
            actual=_text(
                payload.get("candidate_head_commit")
                or payload.get("target_commit")
            ),
            expected=_text(
                current.get("candidate_head_commit")
                or current.get("candidate_head")
            ),
            code="stale_target_commit",
        )
        snapshot_event_id = _text(payload.get("candidate_snapshot_event_id"))
        if snapshot_event_id and snapshot_event_id != current_candidate.id:
            issues.append(_issue(
                "candidate_snapshot_event_id",
                "stale_candidate_snapshot",
                f"current candidate is {current_candidate.id}, got {snapshot_event_id}",
            ))

    if event.type in {"verify.passed", "test.passed"} and not _text(
        payload.get("candidate_anchor_task_id") or payload.get("task_id")
    ):
        issues.append(_issue(
            "candidate_anchor_task_id",
            "candidate_currentness_unprovable",
            "candidate verification has no canonical anchor task",
        ))

    source_index = _source_index(events, event, payload)
    superseding = _later_rework_authority(
        events,
        start_index=source_index,
        workflow_run_id=workflow_run_id,
        payload=payload,
    )
    if superseding is not None:
        issues.append(_issue(
            "authority_event",
            "candidate_result_superseded",
            f"{superseding.type} became authoritative after dispatch",
        ))

    return CandidateSuccessCurrentness(
        applies=True,
        current=not issues,
        issues=tuple(issues),
        superseded_by=superseding.id if superseding is not None else "",
    )


def _is_candidate_success(event: ZfEvent, payload: Mapping[str, Any]) -> bool:
    if event.type not in _CANDIDATE_SUCCESS_EVENTS or event.task_id:
        return False
    if _text(payload.get("authority")) == "compat_projection":
        return False
    return bool(
        payload.get("candidate_currentness_required") is True
        or _text(payload.get("verification_owner")) == "candidate_verify"
        or payload.get("candidate_snapshot_event_id")
    )


def _latest_frozen_candidate(
    events: Sequence[ZfEvent],
    workflow_run_id: str,
) -> ZfEvent | None:
    if not workflow_run_id:
        return None
    from zf.runtime.candidate_freeze_recovery_identity import frozen_candidate_occurrences

    for candidate in frozen_candidate_occurrences(events):
        body = candidate.payload if isinstance(candidate.payload, Mapping) else {}
        candidate_run = _text(
            body.get("workflow_run_id")
            or body.get("trace_id")
            or candidate.correlation_id
        )
        if candidate_run != workflow_run_id:
            continue
        if _text(body.get("schema_version")) == "candidate-freeze-receipt.v1":
            from zf.runtime.candidate_freeze_recovery_identity import native_freeze_for_recovery
            return native_freeze_for_recovery(list(events), candidate)
    return None


def _compare_identity(
    issues: list[dict[str, str]],
    *,
    field: str,
    actual: str,
    expected: str,
    code: str,
    generation: bool = False,
) -> None:
    if not expected:
        return
    matches = (
        same_task_map_generation(actual, expected)
        if generation and actual
        else actual == expected
    )
    if matches:
        return
    issues.append(_issue(
        field,
        code,
        f"current candidate expects {expected}, got {actual or '<missing>'}",
    ))


def _source_index(
    events: Sequence[ZfEvent],
    event: ZfEvent,
    payload: Mapping[str, Any],
) -> int:
    operation_id = _text(payload.get("operation_id"))
    if operation_id:
        for index, candidate in enumerate(events):
            body = candidate.payload if isinstance(candidate.payload, Mapping) else {}
            if (
                candidate.type == "workflow.operation.requested"
                and _text(body.get("operation_id")) == operation_id
            ):
                return index
    for index, candidate in enumerate(events):
        if candidate.id == event.id:
            return index
    return len(events) - 1


def _later_rework_authority(
    events: Sequence[ZfEvent],
    *,
    start_index: int,
    workflow_run_id: str,
    payload: Mapping[str, Any],
) -> ZfEvent | None:
    task_id = _text(
        payload.get("candidate_anchor_task_id") or payload.get("task_id")
    )
    pdd_id = _text(payload.get("pdd_id") or payload.get("feature_id"))
    for candidate in events[start_index + 1:]:
        if candidate.type not in _REWORK_AUTHORITY_EVENTS:
            continue
        body = candidate.payload if isinstance(candidate.payload, Mapping) else {}
        candidate_run = _text(
            body.get("workflow_run_id")
            or body.get("trace_id")
            or candidate.correlation_id
        )
        if workflow_run_id and candidate_run and candidate_run != workflow_run_id:
            continue
        candidate_task = _text(candidate.task_id or body.get("task_id"))
        candidate_pdd = _text(body.get("pdd_id") or body.get("feature_id"))
        if task_id and candidate_task and task_id != candidate_task:
            if not (pdd_id and candidate_pdd and pdd_id == candidate_pdd):
                continue
        if pdd_id and candidate_pdd and pdd_id != candidate_pdd:
            continue
        return candidate
    return None


def _issue(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _text(value: object) -> str:
    return str(value or "").strip()


__all__ = [
    "CandidateSuccessCurrentness",
    "CandidateSuccessCurrentnessMixin",
    "evaluate_candidate_success_currentness",
]
