"""Shared recovery identity, counting, scope, and cap payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_ledger import failure_fingerprint


PRODUCT_FAILURE_CLASS = "product_rejection"
NON_SEMANTIC_FAILURE_CLASSES = frozenset({
    "environment_failure",
    "integration_failure",
    "verifier_execution_failure",
    "verifier_contract_failure",
    "harness_failure",
    "dependency_blocked",
    "candidate_environment_setup_failed",
    "candidate_dependency_missing",
    "candidate_integration_failure",
    "candidate_contract_failure",
})
PRODUCT_SEMANTIC_FAILURE_CLASSES = frozenset({
    PRODUCT_FAILURE_CLASS,
    "candidate_product_quality_failed",
})
_CANDIDATE_OWNER_MARKERS = frozenset({
    "assembly",
    "candidate",
    "candidate_verify",
    "integration",
    "root_owner",
})
_CANDIDATE_ACTIONS = frozenset({"retrigger", "replan", "amend", "supersede"})
_CANDIDATE_EVENT_TYPES = frozenset({
    "candidate.conflict",
    "integration.failed",
    "review.rejected",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "plan.rejected",
})


@dataclass(frozen=True)
class RecoverySeries:
    workflow_run_id: str
    task_id: str
    contract_revision: str
    stage_slot: str
    target_stage_slot: str
    failure_fingerprint: str

    def to_payload(self) -> dict[str, str]:
        return {
            "workflow_run_id": self.workflow_run_id,
            "task_id": self.task_id,
            "contract_revision": self.contract_revision,
            "stage_slot": self.stage_slot,
            "target_stage_slot": self.target_stage_slot,
            "failure_fingerprint": self.failure_fingerprint,
        }


def classify_recovery_scope(event: ZfEvent) -> str:
    """Classify task/candidate/gap without treating task_id as the boundary."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    explicit = str(payload.get("failure_scope") or payload.get("recovery_scope") or "").strip()
    if explicit in {"task", "candidate", "gap"}:
        return explicit
    target_snapshot = payload.get("target_snapshot")
    if isinstance(target_snapshot, Mapping):
        snapshot_scope = str(target_snapshot.get("scope") or "").strip()
        if snapshot_scope in {"task", "candidate", "gap"}:
            return snapshot_scope
    owner = str(payload.get("verification_owner") or payload.get("root_owner_class") or "").strip()
    action = str(payload.get("recovery_action") or payload.get("candidate_rework_action") or "").strip()
    if owner in _CANDIDATE_OWNER_MARKERS or action in _CANDIDATE_ACTIONS:
        return "candidate"
    if payload.get("gap_tasks") or str(payload.get("gap_plan_ref") or "").strip():
        return "gap"
    if (
        event.type == "fanout.child.failed"
        and str(payload.get("reason") or "").strip() == "stale_task_map"
        and str(payload.get("pdd_id") or payload.get("fanout_id") or "").strip()
    ):
        return "candidate"
    if event.type in {"candidate.conflict", "integration.failed"}:
        return "candidate"
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    if event.type in _CANDIDATE_EVENT_TYPES and any(
        str(payload.get(key) or "").strip()
        for key in ("candidate_ref", "candidate_head_commit", "target_ref", "pdd_id")
    ) and not str(payload.get("lane_id") or "").strip() and (
        not task_id
        or bool(payload.get("candidate_ref") or payload.get("candidate_head_commit"))
    ):
        return "candidate"
    return "task" if task_id else "candidate"


def failure_class_from_payload(payload: Mapping[str, Any]) -> str:
    explicit = str(
        payload.get("failure_class")
        or payload.get("failure_classification")
        or ""
    ).strip()
    if explicit:
        return explicit
    result = payload.get("verification_result")
    if isinstance(result, Mapping):
        result_class = str(result.get("failure_class") or "").strip()
        if result_class:
            return result_class
        execution = str(result.get("execution_status") or "")
        verdict = str(result.get("verdict") or "")
        if execution == "failed" or verdict == "abstained":
            return "verifier_execution_failure"
        if verdict == "blocked":
            return "dependency_blocked"
        if execution == "completed" and verdict == "rejected":
            return PRODUCT_FAILURE_CLASS
        if execution == "completed" and verdict == "passed":
            return "none"
    reason = " ".join((
        str(payload.get("reason") or ""),
        str(payload.get("summary") or ""),
    )).lower()
    if any(marker in reason for marker in (
        "timeout", "transport", "pane", "permission denied", "no space left",
    )):
        return "environment_failure"
    if any(marker in reason for marker in (
        "missing task ref", "stale task", "protected path", "admission",
        "invalid json", "schema", "identity", "digest", "workdir",
    )):
        return "harness_failure"
    if any(marker in reason for marker in ("cherry-pick", "integration", "conflict")):
        return "integration_failure"
    return PRODUCT_FAILURE_CLASS


def recovery_series_from_event(event: ZfEvent) -> RecoverySeries:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return RecoverySeries(
        workflow_run_id=str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        ),
        task_id=str(event.task_id or payload.get("task_id") or ""),
        contract_revision=str(payload.get("contract_revision") or "legacy"),
        stage_slot=str(payload.get("stage_slot") or payload.get("stage_id") or ""),
        target_stage_slot=str(
            payload.get("target_stage_slot")
            or payload.get("failure_target")
            or ""
        ),
        failure_fingerprint=failure_fingerprint(event),
    )


def valid_series_failures(
    events: Iterable[ZfEvent],
    series: RecoverySeries,
    *,
    event_types: set[str] | frozenset[str],
) -> list[ZfEvent]:
    """Count valid semantic failures, independent of fanout generation."""

    superseded_fanouts: set[str] = set()
    event_list = list(events)
    for event in event_list:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "fanout.cancelled" and "supersede" in str(payload.get("reason") or ""):
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)
        if event.type == "fanout.child.stale_completion":
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                superseded_fanouts.add(fanout_id)
    out: list[ZfEvent] = []
    seen: set[str] = set()
    for event in event_list:
        if event.type not in event_types:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if bool(payload.get("replay") or payload.get("stale")):
            continue
        if str(payload.get("superseded_by") or "").strip():
            continue
        if failure_class_from_payload(payload) not in PRODUCT_SEMANTIC_FAILURE_CLASSES:
            continue
        current = recovery_series_from_event(event)
        if current.workflow_run_id != series.workflow_run_id:
            continue
        if current.task_id != series.task_id:
            continue
        if current.contract_revision != series.contract_revision:
            continue
        if current.stage_slot != series.stage_slot:
            continue
        if current.target_stage_slot != series.target_stage_slot:
            continue
        if current.failure_fingerprint != series.failure_fingerprint:
            continue
        fanout_id = str(payload.get("fanout_id") or "")
        if fanout_id and fanout_id in superseded_fanouts:
            continue
        identity = str(
            payload.get("result_event_id")
            or payload.get("source_event_id")
            or event.causation_id
            or event.id
        )
        if identity in seen:
            continue
        seen.add(identity)
        out.append(event)
    return out


def rework_dispatch_count(
    events: Iterable[ZfEvent],
    series: RecoverySeries,
    *,
    event_type: str,
) -> int:
    count = 0
    seen: set[str] = set()
    for event in events:
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        workflow_run_id = str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        )
        if workflow_run_id != series.workflow_run_id:
            continue
        if str(event.task_id or payload.get("task_id") or "") != series.task_id:
            continue
        if str(payload.get("contract_revision") or "legacy") != series.contract_revision:
            continue
        if str(payload.get("failed_stage_slot") or payload.get("stage_slot") or "") != series.stage_slot:
            continue
        if str(payload.get("target_stage_slot") or payload.get("failure_target") or "") != series.target_stage_slot:
            continue
        if str(payload.get("failure_fingerprint") or "") != series.failure_fingerprint:
            continue
        source = str(payload.get("lane_stage_event_id") or payload.get("trigger_event_id") or event.id)
        if source in seen:
            continue
        seen.add(source)
        count += 1
    return count


def build_rework_cap_payload(
    *,
    series: RecoverySeries,
    failures: list[ZfEvent],
    max_attempts: int,
    trigger_event: ZfEvent,
    role: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    body: dict[str, Any] = {
        **series.to_payload(),
        "role": role,
        "attempt": len(failures),
        "retry_count": max(0, len(failures) - 1),
        "max_attempts": int(max_attempts),
        "max_attempts_source": "canonical_recovery_series",
        "last_reason": str(payload.get("reason") or trigger_event.type),
        "trigger_event_type": trigger_event.type,
        "trigger_event_id": trigger_event.id,
        "failure_class": PRODUCT_FAILURE_CLASS,
        "failure_count": len(failures),
        "failure_event_ids": [event.id for event in failures],
        "semantic_triage_required": True,
        "recovery_owner": "run_manager",
        "recovery_scope": classify_recovery_scope(trigger_event),
    }
    if extra:
        body.update(dict(extra))
    return body


__all__ = [
    "NON_SEMANTIC_FAILURE_CLASSES",
    "PRODUCT_FAILURE_CLASS",
    "RecoverySeries",
    "build_rework_cap_payload",
    "classify_recovery_scope",
    "failure_class_from_payload",
    "recovery_series_from_event",
    "rework_dispatch_count",
    "valid_series_failures",
]
