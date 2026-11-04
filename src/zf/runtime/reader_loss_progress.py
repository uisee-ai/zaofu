"""Attempt-bound input progress can extend a reader's transport recovery."""
from pathlib import Path
from collections.abc import Callable
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_read_progress import required_read_progress
from zf.runtime.workflow_operation import reduce_workflow_operations


def reader_loss_history(events: list[ZfEvent], role_for_loss: Callable[[ZfEvent], str]) -> tuple[
    dict[tuple[str, str], list[ZfEvent]], set[tuple[str, str]],
    dict[str, list[tuple[int, ZfEvent]]], dict[tuple[str, str], int],
]:
    """Fold current dispatches and loss counts, including controlled reopenings."""
    dispatches: dict[tuple[str, str], list[ZfEvent]] = {}
    terminal_children: set[tuple[str, str]] = set()
    lost_events_by_role: dict[str, list[tuple[int, ZfEvent]]] = {}
    dispatch_lost_count: dict[tuple[str, str], int] = {}
    for index, event in enumerate(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        key = str(payload.get("fanout_id") or ""), str(payload.get("child_id") or "")
        if event.type == "fanout.child.dispatched":
            if all(key):
                dispatches.setdefault(key, []).append(event)
                terminal_children.discard(key)
        elif event.type in {"fanout.child.completed", "fanout.child.failed"}:
            if all(key):
                terminal_children.add(key)
        elif event.type == "fanout.child.dispatch_lost":
            if all(key):
                dispatch_lost_count[key] = dispatch_lost_count.get(key, 0) + 1
        else:
            role_instance = role_for_loss(event)
            if role_instance:
                lost_events_by_role.setdefault(role_instance, []).append((index, event))
    return dispatches, terminal_children, lost_events_by_role, dispatch_lost_count


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
        and event.payload.get("child_id") == child_id), None)
    if previous is None:
        return proof, False
    old = previous.payload
    bound = all(old.get(key) == proof[key] for key in (
        "operation_id", "request_hash", "read_progress_attempt_id"))
    previous_progress = old.get("input_read_progress")
    return proof, bool(bound and type(previous_progress) is int and progress > previous_progress)
