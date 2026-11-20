"""Attempt-bound input progress can extend a reader's transport recovery."""
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_read_progress import required_read_progress
from zf.runtime.workflow_operation import reduce_workflow_operations


def reader_loss_progress(
    state_dir: Path, events: list[ZfEvent], dispatch: ZfEvent, loss: ZfEvent,
) -> tuple[dict[str, Any], bool]:
    """Keep no-progress recovery bounded; never infer progress from tool counts."""
    if (loss.type != "provider.turn.closed" or loss.payload.get("provider_status") != "failed"
            or loss.payload.get("provider_stop_reason") != "transport_error"):
        return {}, False
    payload = dispatch.payload
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    operation_id = str(payload.get("operation_id") or nested.get("operation_id") or "")
    request_hash = str(payload.get("request_hash") or nested.get("request_hash") or "")
    operation = reduce_workflow_operations(events).get(operation_id, {})
    attempt_id = str(operation.get("active_attempt_id") or "")
    if (not request_hash or not attempt_id or operation.get("status") != "running"
            or operation.get("request_hash") != request_hash
            or operation.get("role_instance") != payload.get("role_instance")
            or operation.get("operation_type") not in {"fanout_reader_child", "fanout_synth"}):
        return {}, False
    progress = required_read_progress(state_dir, events, attempt_id=attempt_id,
                                      operation_id=operation_id)
    if progress is None:
        return {}, False
    proof = {"operation_id": operation_id, "request_hash": request_hash,
             "read_progress_attempt_id": attempt_id, "input_read_progress": progress}
    child_id = str(payload.get("child_id") or "synth")
    previous = next((event for event in reversed(events)
        if (event.type == "fanout.child.dispatch_lost" or
            (event.type == "fanout.child.dispatched" and event.origin == "kernel"
             and event.actor == "zf-cli"
             and event.payload.get("recovery_kind") == "operator_reader_checkpoint"))
        and event.payload.get("fanout_id") == payload.get("fanout_id")
        and event.payload.get("child_id") == child_id
        # A deferred send can replay this same loss before redispatch succeeds.
        # Compare against the preceding checkpoint, not its own saved progress.
        and event.payload.get("lost_signal_event_id") != loss.id), None)
    if previous is None:
        return proof, False
    old = previous.payload
    bound = all(old.get(key) == proof[key] for key in (
        "operation_id", "request_hash", "read_progress_attempt_id"))
    previous_progress = old.get("input_read_progress")
    return proof, bool(bound and type(previous_progress) is int and progress > previous_progress)
