"""Proof that a pipeline request never reached scheduler delivery."""

from collections.abc import Iterable, Mapping
import json
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent


def undispatched_pipeline_request(
    operation: Mapping[str, Any], state_dir: Path, events: Iterable[ZfEvent],
) -> bool:
    if (
        operation.get("operation_type") != "task-stage"
        or not operation.get("task_pipeline_stage")
        or not operation.get("request_hash")
        or not operation.get("request_ref")
        or operation.get("started_event_id")
        or operation.get("started_at")
        or operation.get("dispatch_id")
        or operation.get("admitted_call_result_ref")
        or operation.get("admitted_control_result_ref")
    ):
        return False
    if operation.get("status") != "requested" and not (
        operation.get("status") == "cancelled"
        and operation.get("reason") == "workflow_resume_cancelled_stale_operation"
    ):
        return False
    try:
        body = json.loads((state_dir / "task_attempts.json").read_text())
    except FileNotFoundError:
        body = {"attempts": {}}
    except (OSError, ValueError):
        return False
    attempts = body.get("attempts") if isinstance(body, dict) else None
    if not isinstance(attempts, dict):
        return False
    operation_id = str(operation.get("operation_id") or "")
    planned_attempt = str(operation.get("active_attempt_id") or "")
    for event in events:
        if event.type != "task.dispatched" and not event.type.startswith("task.attempt."):
            continue
        payload = event.payload
        if payload.get("operation_id") == operation_id or (
            planned_attempt and payload.get("attempt_id") == planned_attempt
        ):
            return False
    return bool(operation_id) and all(
        isinstance(row, dict)
        and row.get("operation_id") != operation_id
        and (not planned_attempt or key != planned_attempt)
        for key, row in attempts.items()
    )
