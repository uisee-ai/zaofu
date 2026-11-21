"""Canonical writer-fanout plan-admission facts.

The writer fanout owns dispatch.  This module owns the compact, replay-stable
event sequence emitted before a task map is admitted or rejected, keeping
plan-admission recovery out of the already large fanout coordinator.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.workflow.flow_metadata import normalize_flow_kind


def _plan_admission_incident_id(*, stage_id: str, trigger_event_id: str) -> str:
    seed = f"{stage_id}\0{trigger_event_id}".encode("utf-8")
    return "plan-admission-" + hashlib.sha256(seed).hexdigest()[:20]


def _task_map_identity(
    *,
    task_map_ref: str,
    task_map_path: Path | None = None,
) -> dict[str, str]:
    """Describe a task-map artifact without treating its contents as truth."""

    digest = ""
    if task_map_path is not None:
        try:
            digest = hashlib.sha256(task_map_path.read_bytes()).hexdigest()
        except OSError:
            digest = ""
    return {
        "task_map_ref": str(task_map_ref or ""),
        "task_map_digest": digest,
    }


def _plan_artifact_package_identity(payload: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(payload.get(key) or "")
        for key in (
            "plan_artifact_package_id",
            "plan_artifact_package_ref",
            "plan_artifact_package_digest",
        )
        if str(payload.get(key) or "")
    }


def emit_upstream_failure_for_bad_task_map(
    coordinator: Any,
    *,
    trigger_event: ZfEvent,
    trace_id: str,
    pdd_id: str,
    reason: str,
    stage_id: str = "",
    plan_admission_incident_id: str = "",
    task_map_ref: str = "",
    task_map_digest: str = "",
) -> ZfEvent | None:
    """Emit the topology-selected plan failure for a rejected task map."""

    trigger_payload = (
        trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    )
    failure_event = ""
    upstream_stage_id = ""
    gap_event_type = str(trigger_payload.get("gap_event_type") or "").strip()
    if str(trigger_payload.get("resume_scope") or "") == "gap_tasks_only":
        failure_event = {
            "flow.gap_plan.ready": "flow.discovery.failed",
            "goal.gap_plan.ready": "flow.discovery.failed",
            "gap_plan.ready": "module.parity.scan.failed",
        }.get(gap_event_type, "")
    stages = getattr(coordinator.config.workflow, "stages", []) or []
    trigger_flow_kind = normalize_flow_kind(
        trigger_payload.get("flow_kind")
        or trigger_payload.get("kind")
        or trigger_payload.get("request_kind")
    )
    if failure_event:
        for candidate in stages:
            aggregate = getattr(candidate, "aggregate", None)
            candidate_failure = str(
                getattr(candidate, "failure_event", "")
                or getattr(aggregate, "failure_event", "")
                or ""
            )
            if candidate_failure == failure_event:
                upstream_stage_id = str(getattr(candidate, "id", "") or "")
                break
    else:
        for candidate in stages:
            candidate_flow_kind = normalize_flow_kind(
                getattr(candidate, "flow_kind", "")
            )
            if (
                trigger_flow_kind
                and candidate_flow_kind
                and candidate_flow_kind != trigger_flow_kind
            ):
                continue
            aggregate = getattr(candidate, "aggregate", None)
            success = str(
                getattr(candidate, "success_event", "")
                or getattr(aggregate, "success_event", "")
                or ""
            )
            if success == trigger_event.type:
                failure_event = str(
                    getattr(candidate, "failure_event", "")
                    or getattr(aggregate, "failure_event", "")
                    or ""
                )
                upstream_stage_id = str(getattr(candidate, "id", "") or "")
                break
    if not failure_event:
        return None
    try:
        for existing in coordinator.event_log.read_all():
            if existing.type != failure_event:
                continue
            payload = existing.payload if isinstance(existing.payload, dict) else {}
            if str(payload.get("trigger_event_id") or "") == trigger_event.id:
                return existing
    except Exception:
        pass

    source_refs = (
        trigger_payload.get("source_refs")
        if isinstance(trigger_payload.get("source_refs"), dict)
        else {}
    )
    failure = ZfEvent(
        type=failure_event,
        actor="zf-cli",
        payload={
            "stage_id": upstream_stage_id,
            "writer_stage_id": stage_id,
            "status": "failed",
            "trigger_event_id": trigger_event.id,
            "trace_id": trace_id,
            "pdd_id": pdd_id,
            "failure_scope": "plan_admission",
            "plan_admission_incident_id": plan_admission_incident_id,
            "task_map_ref": task_map_ref or str(
                trigger_payload.get("task_map_ref") or ""
            ),
            "task_map_digest": task_map_digest,
            "target_ref": str(trigger_payload.get("target_ref") or ""),
            "source_refs": dict(source_refs),
            "gap_event_type": gap_event_type,
            "resume_scope": str(trigger_payload.get("resume_scope") or ""),
            "gap_plan_ref": str(trigger_payload.get("gap_plan_ref") or ""),
            **_plan_artifact_package_identity(trigger_payload),
            "reason": f"task_map rejected by writer fanout admission: {reason}",
            "findings": [{
                "severity": "high",
                "message": (
                    "task_map artifact must be valid JSON matching the "
                    "task-map contract; got: " + reason[:200]
                ),
            }],
        },
        causation_id=trigger_event.id,
        correlation_id=trace_id or None,
    )
    coordinator.event_writer.append(failure)
    return failure


def emit_plan_admission_cancel(
    coordinator: Any,
    *,
    trigger_event: ZfEvent,
    stage_id: str,
    trace_id: str,
    pdd_id: str,
    feature_id: str,
    task_map_ref: str,
    reason: str,
    task_map_path: Path | None = None,
    source_index_ref: str = "",
    wave: int | None = None,
    task_ids: list[str] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> ZfEvent:
    """Emit canonical upstream failure followed by the raw fanout cancellation."""

    trigger_payload = (
        trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    )
    incident_id = str(
        trigger_payload.get("plan_admission_incident_id") or ""
    ).strip() or _plan_admission_incident_id(
        stage_id=stage_id,
        trigger_event_id=trigger_event.id,
    )
    identity = _task_map_identity(
        task_map_ref=task_map_ref,
        task_map_path=task_map_path,
    )
    task_state_conflict = reason == "terminal_tasks"
    previous = None
    if task_state_conflict:
        previous = next((e for e in coordinator.event_log.read_all()
                         if e.type == "fanout.cancelled" and e.payload.get("trigger_event_id") == trigger_event.id
                         and e.payload.get("stage_id") == stage_id
                         and e.payload.get("plan_admission_incident_id") == incident_id
                         and e.payload.get("failure_scope") == "task_state_admission"), None)
    canonical_failure = None if task_state_conflict else emit_upstream_failure_for_bad_task_map(
        coordinator,
        trigger_event=trigger_event,
        trace_id=trace_id,
        pdd_id=pdd_id,
        reason=reason,
        stage_id=stage_id,
        plan_admission_incident_id=incident_id,
        task_map_ref=identity["task_map_ref"],
        task_map_digest=identity["task_map_digest"],
    )
    payload: dict[str, Any] = {
        "stage_id": stage_id,
        "trigger_event_id": trigger_event.id,
        "trace_id": trace_id,
        "pdd_id": pdd_id,
        "feature_id": feature_id or pdd_id,
        **({"workflow_run_id": str(trigger_payload.get("workflow_run_id") or trace_id)} if task_state_conflict else {}),
        "source_index_ref": source_index_ref,
        "task_ids": [task_id for task_id in task_ids or [] if task_id],
        "failure_scope": "task_state_admission" if task_state_conflict else "plan_admission",
        "plan_admission_incident_id": incident_id,
        "canonical_failure_event_id": (
            canonical_failure.id if canonical_failure is not None else ""
        ),
        "gap_event_type": str(trigger_payload.get("gap_event_type") or ""),
        "resume_scope": str(trigger_payload.get("resume_scope") or ""),
        "gap_plan_ref": str(trigger_payload.get("gap_plan_ref") or ""),
        "reason": reason,
        **identity,
        **_plan_artifact_package_identity(trigger_payload),
    }
    if wave is not None:
        payload["wave"] = wave
    if extra_payload:
        payload.update(extra_payload)
    cancelled = previous or ZfEvent(
        type="fanout.cancelled",
        actor="zf-cli",
        origin="kernel" if task_state_conflict else "",
        task_id=pdd_id or None,
        payload=payload,
        causation_id=trigger_event.id,
        correlation_id=trace_id or None,
    )
    if previous is None:
        coordinator.event_writer.append(cancelled)
    if task_state_conflict:
        if not any(e.type == "task.fanout.rejected" and e.origin == "kernel"
                   and e.causation_id == cancelled.id for e in coordinator.event_log.read_all()):
            coordinator.event_writer.append(ZfEvent(type="task.fanout.rejected", origin="kernel", actor="zf-cli",
                task_id=pdd_id or None, causation_id=cancelled.id, correlation_id=trace_id or None,
                payload={**cancelled.payload, "workflow_run_id": str(trigger_payload.get("workflow_run_id") or trace_id),
                         "recovery_owner": "run_manager", "diagnosis_status": "required",
                         "reason": "terminal_tasks", "source_event_id": cancelled.id}))
    elif canonical_failure is None:
        _emit_unroutable_plan_admission_escalation(
            coordinator,
            cancelled=cancelled,
            incident_id=incident_id,
            trace_id=trace_id,
            reason=reason,
        )
    return cancelled


def _emit_unroutable_plan_admission_escalation(
    coordinator: Any,
    *,
    cancelled: ZfEvent,
    incident_id: str,
    trace_id: str,
    reason: str,
) -> None:
    """Surface a rejected plan when no declared upstream recovery edge exists."""

    events = coordinator.event_log.read_all()
    if any(
        event.type == "human.escalate"
        and isinstance(event.payload, dict)
        and str(event.payload.get("plan_admission_incident_id") or "")
        == incident_id
        for event in events
    ):
        return
    failure_class = (
        "plan_artifact_package_rejected"
        if "artifact package" in reason.lower()
        else "plan_admission_failed"
    )
    escalation_reason = (
        "plan admission rejected and no upstream failure route is declared; "
        "operator recovery is required"
    )
    coordinator.event_writer.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        causation_id=cancelled.id,
        correlation_id=trace_id or None,
        payload={
            "failure_class": failure_class,
            "failure_scope": "plan_admission",
            "reason": f"{escalation_reason}: {reason}",
            "source_event_id": cancelled.id,
            "plan_admission_incident_id": incident_id,
            "recovery_owner": "operator",
            "allowed_actions": ["operator_review", "start_new_generation"],
            "max_auto_attempts": 0,
            "max_rescans": 0,
            "terminalization_condition": "immediate",
            "operator_required": True,
            "recoverable": False,
        },
    ))


def emit_task_map_admitted(
    coordinator: Any,
    *,
    trigger_event: ZfEvent,
    stage_id: str,
    trace_id: str,
    loaded: Any,
    task_items: list[dict[str, Any]],
) -> ZfEvent:
    """Record a replay-stable fact that a task map passed writer admission."""

    for existing in reversed(coordinator.event_log.read_all()):
        if existing.type != "task_map.admitted":
            continue
        payload = existing.payload if isinstance(existing.payload, dict) else {}
        if (
            str(payload.get("stage_id") or "") == stage_id
            and str(payload.get("trigger_event_id") or "") == trigger_event.id
        ):
            return existing

    trigger_payload = (
        trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    )
    recovered_from = str(
        trigger_payload.get("plan_admission_incident_id") or ""
    ).strip()
    incident_id = recovered_from or _plan_admission_incident_id(
        stage_id=stage_id,
        trigger_event_id=trigger_event.id,
    )
    identity = _task_map_identity(
        task_map_ref=str(getattr(loaded, "task_map_ref", "") or ""),
        task_map_path=getattr(loaded, "task_map_path", None),
    )
    admitted = ZfEvent(
        type="task_map.admitted",
        actor="zf-cli",
        payload={
            "schema_version": "task-map.admission.v1",
            "stage_id": stage_id,
            "trigger_event_id": trigger_event.id,
            "trace_id": trace_id,
            "pdd_id": str(getattr(loaded, "pdd_id", "") or ""),
            "feature_id": str(
                getattr(loaded, "feature_id", "")
                or getattr(loaded, "pdd_id", "")
                or ""
            ),
            "source_index_ref": str(
                getattr(loaded, "source_index_ref", "") or ""
            ),
            "task_ids": [
                str(item.get("task_id") or "")
                for item in task_items
                if str(item.get("task_id") or "")
            ],
            "plan_admission_incident_id": incident_id,
            "recovered_from_incident_id": recovered_from,
            **identity,
        },
        causation_id=trigger_event.id,
        correlation_id=trace_id or None,
    )
    coordinator.event_writer.append(admitted)
    return admitted


__all__ = [
    "emit_plan_admission_cancel",
    "emit_task_map_admitted",
    "emit_upstream_failure_for_bad_task_map",
]
