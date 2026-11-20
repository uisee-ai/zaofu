"""Deliver a result correction on its existing transport attempt, once."""
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.provider_turn_liveness import _parse_event_ts
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.transport import DispatchContext
from zf.runtime.workflow_operation import reduce_workflow_operations


def prepare_repair_continuation(runtime: Any, context: Any, outcome: Any, prompt: str):
    """Claim before sending; an uncertain send must not replay paid work."""
    if not isinstance(context, DispatchContext) or not context.task_id:
        return context
    with locked_path(runtime.state_dir / "locks" / "result-repair-delivery"):
        events = runtime.event_log.read_all()
        digest = outcome.correction_ref.get("sha256")
        if any(e.type == "worker.continuation.claimed" and e.origin == "kernel"
               and e.payload.get("operation_id") == outcome.operation_id
               and e.payload.get("correction_digest") == digest for e in events):
            return None
        rows = [r for r in task_attempt_store(runtime).current_rows()
                if r.get("operation_id") == context.operation_id
                and r.get("dispatch_id") == context.dispatch_id
                and r.get("instance_id") == context.instance_id]
        if len(rows) != 1:
            raise RuntimeError("result correction requires a unique current TaskAttempt")
        row = rows[0]
        context = replace(context, continuation=True, attempt_id=row["attempt_id"], lease_id=row["lease_id"])
        claim = ZfEvent(type="worker.continuation.claimed", origin="kernel",
            actor=context.instance_id, task_id=context.task_id, correlation_id=context.run_id,
            payload={**context.to_payload(), "workflow_run_id": context.run_id,
                "recovery_action": "result_protocol_correction", "request_hash": outcome.request_hash,
                "correction_digest": digest, "correction_ref": outcome.correction_ref,
                "delivery_status": "unknown", "business_completed": False,
                "next_action": "reconcile_existing_turn_before_retry"})
        validate_repair_continuation(runtime, context.instance_id, context, events, claim)
        runtime.event_writer.append(claim)
        from zf.runtime.continuation_delivery import prepare_continuation_delivery
        return prepare_continuation_delivery(runtime, context, prompt, claim.id)


def validate_repair_continuation(runtime: Any, role: str, context: DispatchContext,
                                 events: list[ZfEvent], claim: ZfEvent) -> None:
    if any(e.type == "event.malformed" and e.payload.get("ledger_integrity") == "unknown" for e in events):
        raise RuntimeError("result correction requires a trustworthy ledger")
    operation = reduce_workflow_operations(events).get(context.operation_id, {})
    packet = hydrate_sidecar_ref(runtime.state_dir, claim.payload["correction_ref"]).payload
    expected = {"operation_id": context.operation_id, "workflow_run_id": context.run_id,
                "dispatch_id": context.dispatch_id, "request_hash": operation.get("request_hash"),
                "attempt_id": operation.get("active_attempt_id")}
    requests = [e for e in events if e.type == "workflow.call.result.repair.requested"
                and e.payload.get("operation_id") == context.operation_id]
    if (claim.origin != "kernel" or claim.actor != role
            or operation.get("status") != "running" or operation.get("role_instance") != role
            or operation.get("task_id") != context.task_id
            or operation.get("workflow_run_id") != context.run_id
            or operation.get("dispatch_id") != context.dispatch_id
            or any(not v or packet.get(k) != v for k, v in expected.items())
            or claim.payload.get("request_hash") != expected["request_hash"]
            or not requests or requests[-1].payload.get("correction_ref") != claim.payload["correction_ref"]
            or any(e.type == "workflow.call.result.invalid" and e.payload.get("operation_id") == context.operation_id
                   and e.payload.get("request_hash") == expected["request_hash"] for e in events)
            or any(e.type == "workflow.call.result.repair.dispatched"
                   and e.payload.get("correction_ref") == claim.payload["correction_ref"] for e in events)):
        raise RuntimeError("result correction operation identity is stale")
    row = task_attempt_store(runtime).get(str(context.attempt_id or "")) or {}
    binding = {"run_id": context.run_id, "task_id": context.task_id, "operation_id": context.operation_id,
               "dispatch_id": context.dispatch_id, "attempt_id": context.attempt_id,
               "lease_id": context.lease_id, "instance_id": role}
    expiry = _parse_event_ts(row.get("lease_expires_at"))
    if (row.get("status") != "sent" or any(not v or row.get(k) != v for k, v in binding.items())
            or expiry is None or expiry <= datetime.now(timezone.utc)):
        raise RuntimeError("result correction requires the current sent and unexpired TaskAttempt")
    if operation.get("operation_type") == "task-stage":
        task = runtime.task_store.get(context.task_id)
        if (task is None or task.status != "in_progress" or task.assigned_to != role
                or task.active_dispatch_id != context.dispatch_id):
            raise RuntimeError("result correction task owner is stale")
    if not runtime.transport.is_alive(role):
        raise RuntimeError("result correction requires the existing live worker")
