"""Separate an immutable Reader input attempt from its admitted transport retry."""
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent


def reader_input_dispatch_matches(
    operation: Mapping[str, Any], manifest: Mapping[str, Any], events: Sequence[ZfEvent],
) -> bool:
    if not manifest.get("dispatch_id") or not operation.get("dispatch_id"):
        return False
    if operation["dispatch_id"] == manifest["dispatch_id"]:
        return True
    # The caller also proves the immutable request/manifest digest and unchanged
    # attempt/lease. Only a causal admitted redrive can replace the transport ID.
    scoped = [e for e in events if e.payload.get("operation_id") == operation.get("operation_id")]
    started = next((e for e in reversed(scoped) if e.type == "workflow.operation.started"), None)
    if started is None or not started.causation_id or any(
        started.payload.get(key) != operation.get(key) for key in (
            "workflow_run_id", "request_hash", "dispatch_id", "active_attempt_id", "lease_id", "role_instance"
        )
    ):
        return False
    return any(e.type == "workflow.operation.redrive_admitted"
        and e.payload.get("source_attempt_id") == manifest.get("attempt_id")
        and e.payload.get("recovery_decision_event_id") == started.causation_id
        and all(e.payload.get(key) == operation.get(key) for key in (
            "workflow_run_id", "request_hash", "task_id"
        )) for e in scoped[:scoped.index(started)])
