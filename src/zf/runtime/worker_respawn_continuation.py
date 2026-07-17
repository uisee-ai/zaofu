"""Deliver an immutable rework continuation after provider-session respawn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent


_IDENTITY_KEYS = (
    "workflow_run_id",
    "contract_revision",
    "stage_slot",
    "target_stage_slot",
    "failure_fingerprint",
    "attempt",
    "max_attempts",
    "feedback_id",
    "finding_ids",
)


def deliver_respawn_continuation(
    runtime: Any,
    request: ZfEvent,
    *,
    instance_id: str,
) -> None:
    payload = request.payload if isinstance(request.payload, dict) else {}
    raw_ref = str(payload.get("continuation_briefing_ref") or "").strip()
    if not raw_ref:
        return
    briefing_path = Path(raw_ref).resolve()
    state_root = Path(runtime.state_dir).resolve()
    if not briefing_path.is_relative_to(state_root):
        raise ValueError("continuation briefing must stay under project.state_dir")
    if not briefing_path.is_file():
        raise FileNotFoundError(f"continuation briefing missing: {briefing_path}")
    task_id = str(request.task_id or payload.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("respawn continuation requires task_id")
    runtime.transport.send_task(
        instance_id,
        briefing_path,
        f"REWORK CONTINUATION for {task_id} — read {briefing_path}",
    )
    shared: dict[str, Any] = {
        "task_id": task_id,
        "assignee": instance_id,
        "dispatch_id": str(payload.get("dispatch_id") or ""),
        "delivery_mode": "resume_session",
        "rework_request_event_id": str(
            payload.get("rework_request_event_id") or request.causation_id or ""
        ),
        "briefing": str(briefing_path),
        "rework_feedback_ref": str(payload.get("rework_feedback_ref") or ""),
        "rework_feedback_digest": str(payload.get("rework_feedback_digest") or ""),
    }
    for key in _IDENTITY_KEYS:
        if key in payload and payload[key] not in (None, "", []):
            shared[key] = payload[key]
    runtime.event_writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id=task_id,
        payload={**shared, "source": "rework_continuation"},
        causation_id=request.id,
        correlation_id=request.correlation_id,
    ))
    runtime.event_writer.append(ZfEvent(
        type="task.rework.continuation_injected",
        actor="zf-cli",
        task_id=task_id,
        payload={
            **shared,
            "lane": instance_id,
            "reason": "canonical rework delivered after provider session resume",
        },
        causation_id=request.id,
        correlation_id=request.correlation_id,
    ))


__all__ = ["deliver_respawn_continuation"]
