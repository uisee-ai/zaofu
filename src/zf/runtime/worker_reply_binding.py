"""Validate explicit operation targets immediately before operator guidance."""
from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent

from zf.runtime.workflow_operation import reduce_workflow_operations


_OPERATION_BINDINGS = {
    "attempt_id": "active_attempt_id",
    "request_hash": "request_hash",
    "dispatch_id": "dispatch_id",
    "lease_id": "lease_id",
    "workflow_run_id": "workflow_run_id",
    "run_id": "workflow_run_id",
}


def reply_operation_fields(payload: dict) -> dict:
    """Select explicit operation bindings for a worker.reply.requested event."""
    return {key: payload[key] for key in ("operation_id", *_OPERATION_BINDINGS) if key in payload}


def resolve_reply_operation(runtime: Any, instance_id: str, payload: dict, *, exact: bool = False, operations=None) -> dict:
    """Unbound resident messages remain valid; explicit targets never fall back."""
    operation_id = str(payload.get("operation_id") or "").strip()
    if not operation_id:
        return {}
    if operations is None:
        operations = reduce_workflow_operations(runtime.event_log.read_all())
    operation = operations.get(operation_id)
    if operation is None:
        raise ValueError("reply target operation not found")
    if operation.get("status") != "running":
        raise ValueError("reply target operation is not running")
    if operation.get("call_result_admitted_event_id") or operation.get("admitted_call_result_ref"):
        raise ValueError("reply target result is already admitted")
    if operation.get("role_instance") != instance_id:
        raise ValueError("reply target worker changed")
    for supplied, current in (("task_id", "task_id"), *_OPERATION_BINDINGS.items()):
        value = str(payload.get(supplied) or "").strip()
        if (value or (exact and supplied in payload)) and value != str(operation.get(current) or ""):
            raise ValueError(f"reply target {supplied} changed")
    return operation


def _web_reply_target(payload: dict, original: dict) -> dict:
    # Pending Web replies from before explicit bindings carried them only in the request copy.
    fields = reply_operation_fields(original)
    for key in ("instance_id", "task_id"):
        if key in original:
            fields[key] = original[key]
    for key, value in fields.items():
        if key in payload and str(payload[key] or "").strip() != str(value or "").strip():
            raise ValueError(f"conflicting reply target {key}")
    target = {**fields, **payload}
    if not str(target.get("operation_id") or "").strip() and any(
        str(target.get(key) or "").strip() for key in _OPERATION_BINDINGS
    ):
        raise ValueError("operation_id is required for an operation-specific reply")
    return target


def admit_worker_reply(runtime: Any, request: ZfEvent, instance_id: str, payload: dict) -> dict | None:
    try:
        original = payload.get("request")
        if isinstance(original, dict):
            payload = _web_reply_target(payload, original)
        return resolve_reply_operation(runtime, instance_id, payload)
    except ValueError as exc:
        runtime._emit_worker_action_failed(
            request, "worker.reply.failed", instance_id=instance_id, reason=str(exc),
        )
        return None


def notification_authority(runtime, request_id, instance_id, payload, events):
    from zf.runtime.call_result_envelope import canonical_json_sha256
    from zf.runtime.workflow_board import ACTOR as board_actor, current_board_identity, _binding
    from zf.runtime.workflow_schedule import ACTOR as scheduler_actor, definitions

    original = next((e for e in events if e.id == request_id and e.type == "worker.reply.requested"
                     and e.origin == "kernel" and e.actor in {board_actor, scheduler_actor}), None)
    if original is None or (payload is not None and original.payload != payload):
        raise ValueError("notification has no trusted request")
    payload = original.payload
    op = resolve_reply_operation(runtime, instance_id, payload)
    if not op or op.get("request_hash") != payload.get("request_hash"):
        raise ValueError("notification operation changed")
    cause = next((e for e in events if e.id == original.causation_id and e.origin == "kernel"
                  and e.actor == original.actor), None)
    if original.actor == board_actor:
        op, board = current_board_identity(runtime, {**payload, "actor": instance_id}, events=events)
        if (not cause or cause.type != "workflow.board.posted"
                or cause.payload.get("audience", {}).get(instance_id) != _binding(op)
                or board["generation"] != payload.get("generation")
                or cause.payload["message_key"] != payload.get("message_key")):
            raise ValueError("notification audience changed")
    else:
        if not cause or cause.type != "workflow.schedule.claimed" or not any(
            d.get("enabled") and d["id"] == cause.payload["definition_id"] and
            canonical_json_sha256({k: v for k, v in d.items() if k != "enabled"}) == cause.payload["definition_digest"]
            for d in definitions(runtime.config)):
            raise ValueError("wait was disabled or superseded")
    return original, op


def claim_bound_notification(runtime: Any, request: ZfEvent, instance_id: str, payload: dict) -> ZfEvent | None:
    """Journal an actual transport send before bytes leave. An unknown send needs reconciliation."""
    from zf.core.state.locks import locked_path
    from zf.runtime.call_result_envelope import canonical_json_sha256
    key = payload.get("notification_key")
    if not key:
        return
    with locked_path(runtime.state_dir / "locks" / "notification" / canonical_json_sha256(key)):
        events = runtime.event_log.read_all()
        original, op = notification_authority(runtime, request.id, instance_id, payload, events)
        if any(e.origin == "kernel" and e.type == "workflow.notification.claimed" and
               e.payload.get("notification_key") == key for e in events):
            raise ValueError("prior send outcome requires reconciliation before retry")
        return runtime.event_writer.append(ZfEvent(type="workflow.notification.claimed", actor="zf-cli",
            origin="kernel", task_id=op.get("task_id"), causation_id=original.id,
            correlation_id=op["workflow_run_id"], payload={"notification_key": key,
                "operation_id": op["operation_id"], "attempt_id": op["active_attempt_id"],
                "request_hash": op["request_hash"], "instance_id": instance_id, "dispatch_id": op.get("dispatch_id"),
                "lease_id": op.get("lease_id"), "workflow_run_id": op["workflow_run_id"],
                "owner": "run_manager", "delivery_status": "unknown_until_transport_receipt"}))


def notification_context(runtime, request, actor, payload, context):
    from dataclasses import replace

    claim = claim_bound_notification(runtime, request, actor, payload)
    binding = claim.payload
    return replace(context, continuation=True, continuation_id=claim.id,
                   operation_id=binding["operation_id"], attempt_id=binding["attempt_id"],
                   dispatch_id=binding["dispatch_id"], lease_id=binding["lease_id"], run_id=binding["workflow_run_id"])


def write_reply_briefing(state, actor, task_id, request_id, message, *, notice):
    from zf.core.state.atomic_io import atomic_write_text
    from zf.runtime.call_result_envelope import canonical_json_sha256

    heading = "Run notification" if notice else "Operator Reply"
    folder = state / "operator-replies"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{actor}-{canonical_json_sha256(request_id)[:20]}.md"
    atomic_write_text(path, f"# {heading}\n\n- worker: `{actor}`\n- task_id: `{task_id}`\n\n## Message\n{message}\n")
    return path, f"{heading} for {actor}.\nRead `{path}` and continue the current task accordingly."
