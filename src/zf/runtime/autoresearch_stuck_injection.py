"""Kernel validation for external controlled-stuck requests."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from zf.runtime.orchestrator_agent_role import is_configured_orchestrator_role


def handle_autoresearch_stuck_injection(
    runtime: Any,
    event: ZfEvent,
) -> WorkflowRuntimeDecision | None:
    persisted_events = runtime.event_log.read_all()
    persisted = next(
        (
            candidate
            for candidate in persisted_events
            if candidate.id == event.id
            and candidate.type == "autoresearch.inject.worker_stuck"
        ),
        None,
    )
    if persisted is None:
        return _block(
            event,
            "request is not present in the canonical event log",
        )
    event = persisted
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.origin != "external":
        return _block(event, "external origin required")
    source = str(payload.get("source") or event.actor or "")
    if source not in {"autoresearch", "zf-autoresearch"}:
        return _block(event, "invalid source")

    trigger_event_id = str(payload.get("trigger_event_id") or "")
    if not trigger_event_id or event.causation_id != trigger_event_id:
        return _block(
            event,
            "causation must reference payload.trigger_event_id",
        )
    dispatch = next(
        (
            candidate
            for candidate in persisted_events
            if candidate.id == trigger_event_id
        ),
        None,
    )
    if dispatch is None or dispatch.type != "task.dispatched":
        return _block(event, "triggering task.dispatched event not found")

    instance_id = str(
        payload.get("instance_id")
        or payload.get("target_instance")
        or event.actor
        or ""
    )
    role = runtime._find_role_by_instance(instance_id) or runtime._find_role_by_name(
        instance_id
    )
    if role is None or is_configured_orchestrator_role(runtime.config, role):
        return _block(event, "unknown worker", role=instance_id)

    dispatch_payload = dispatch.payload if isinstance(dispatch.payload, dict) else {}
    dispatch_id = str(payload.get("dispatch_id") or "")
    dispatch_role = str(dispatch_payload.get("role") or "")
    if (
        not event.task_id
        or dispatch.task_id != event.task_id
        or event.correlation_id != dispatch.correlation_id
        or str(dispatch_payload.get("dispatch_id") or "") != dispatch_id
        or str(dispatch_payload.get("assignee") or "") != role.instance_id
        or dispatch_role not in {role.name, role.instance_id}
        or str(payload.get("role") or "") != role.name
    ):
        return _block(
            event,
            "dispatch identity does not match request",
            role=role.instance_id,
        )

    active_task = runtime._active_task_for_instance(role.instance_id)
    if active_task is None:
        return WorkflowRuntimeDecision(
            action="skip",
            task_id=event.task_id,
            role=role.instance_id,
            reason=("autoresearch stuck injection skipped: worker has no active task"),
        )
    if event.task_id != active_task.id:
        return _block(
            event,
            f"task does not match active task {active_task.id}",
            role=role.instance_id,
        )

    latest_dispatch = runtime._latest_dispatch_event_for_task(active_task.id)
    if (
        active_task.status != "in_progress"
        or active_task.assigned_to != role.instance_id
        or active_task.active_dispatch_id != dispatch_id
        or latest_dispatch is None
        or latest_dispatch.id != dispatch.id
    ):
        return _block(
            event,
            "request does not match current dispatch",
            role=role.instance_id,
        )

    attempt, attempt_error = _current_attempt(
        runtime,
        task_id=active_task.id,
        dispatch_id=dispatch_id,
    )
    task_attempt_config = getattr(
        getattr(runtime.config, "workflow", None),
        "task_attempt",
        None,
    )
    if getattr(task_attempt_config, "mode", "shadow") == "enforce" and attempt is None:
        suffix = f": {attempt_error}" if attempt_error else ""
        return _block(
            event,
            f"current TaskAttempt lease is unavailable{suffix}",
            role=role.instance_id,
        )
    if attempt is not None and (
        str(attempt.get("dispatch_id") or "") != dispatch_id
        or str(attempt.get("instance_id") or "") != role.instance_id
        or str(attempt.get("role") or "") != role.name
        or str(attempt.get("status") or "") not in {"prepared", "delivering", "sent"}
    ):
        return _block(
            event,
            "current TaskAttempt lease does not match",
            role=role.instance_id,
        )

    return runtime._report_stuck_worker(
        role,
        trigger_event=event,
        source="autoresearch",
        reason=str(payload.get("reason") or "controlled autoresearch stuck injection"),
    )


def _current_attempt(
    runtime: Any,
    *,
    task_id: str,
    dispatch_id: str,
) -> tuple[dict[str, Any] | None, str]:
    try:
        from zf.runtime.task_attempt_runtime import task_attempt_store

        return (
            task_attempt_store(runtime).current_for_task(
                task_id,
                dispatch_id=dispatch_id,
            ),
            "",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return None, str(exc)


def _block(
    event: ZfEvent,
    reason: str,
    *,
    role: str = "",
) -> WorkflowRuntimeDecision:
    return WorkflowRuntimeDecision(
        action="block",
        task_id=event.task_id,
        role=role or None,
        reason=f"autoresearch stuck injection rejected: {reason}",
    )


__all__ = ["handle_autoresearch_stuck_injection"]
