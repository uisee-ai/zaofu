"""Bind a failed provider turn to its exact still-live Task Pipeline attempt."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.provider_turn_liveness import _parse_event_ts
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.workflow_operation import load_workflow_operation


def bind_failed_task_pipeline_turn(runtime: Any, event: ZfEvent) -> ZfEvent | None:
    payload = event.payload
    if (payload.get("backend") != "codex" or payload.get("provider_status") != "failed"
            or not payload.get("provider_stop_reason") or not payload.get("turn_id")):
        return None
    attempts = [row for row in task_attempt_store(runtime).current_rows()
                if row.get("identity_version") == "operation-v2"
                and row.get("instance_id") == event.actor and row.get("status") == "sent"]
    if len(attempts) != 1:
        return None
    attempt = attempts[0]
    created = _parse_event_ts(attempt.get("created_at"))
    closed = _parse_event_ts(event.ts)
    prompts = [row for row in runtime.event_log.read_all()
               if row.type == "codex.hook.user_prompt_submit" and row.actor == event.actor]
    prompt = prompts[-1] if prompts else None
    opened = _parse_event_ts(prompt.ts) if prompt else None
    if (not created or not closed or not opened or not created <= opened <= closed
            or prompt.payload.get("turn_id") != payload["turn_id"]):
        return None
    operation = load_workflow_operation(runtime.event_log, str(attempt.get("operation_id") or ""))
    if (not operation or operation.get("status") != "running"
            or operation.get("task_id") != attempt.get("task_id")
            or operation.get("dispatch_id") != attempt.get("dispatch_id")
            or operation.get("workflow_run_id") != attempt.get("run_id")):
        return None
    # Keep the original occurrence identity and causation; this is an internal
    # binding, not a second provider event or a fabricated semantic result.
    return replace(event,
        task_id=attempt["task_id"], correlation_id=attempt["run_id"],
        payload={**payload, **{key: attempt[key] for key in (
            "operation_id", "attempt_id", "lease_id", "dispatch_id", "instance_id",
        )}, "workflow_run_id": attempt["run_id"]},
    )
