"""Continue bound reader progress; never count a delivered page as a verdict."""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.attempt_read_progress import required_read_progress
from zf.runtime.pause_lifecycle import is_dispatch_paused


def continue_progressing_reader(runtime, *, events, manifest, child, dispatch,
                               operation, closed, attempt: int) -> bool | None:
    """Called under the fanout recovery sweep's existing controller lock.

    True means a mutation, None means wait without blocking other readers,
    and False leaves recovery to the existing fault path. Unknown delivery stays claimed;
    it cannot be interpreted as permission to resend or as product completion.
    Reads are finite, unique coverage of the unchanged frozen input, not tokens,
    repeated tool calls, chat activity, or an assertion about model memory.
    """
    if is_dispatch_paused(runtime.state_dir, events=events):
        return None
    p = dispatch.payload
    role = str(p.get("role_instance") or "")
    nested = p.get("payload") or {}
    attempt_id = str(operation.get("active_attempt_id") or "")
    operation_id = str(operation.get("operation_id") or "")
    request_hash = str(operation.get("request_hash") or "")
    if (not all((role, attempt_id, operation_id, request_hash))
            or operation.get("status") != "running"
            or operation.get("role_instance") != role
            or p.get("operation_id") != operation_id
            or (p.get("request_hash") or nested.get("request_hash")) != request_hash
            or (nested.get("attempt_id") or p.get("attempt_id")) != attempt_id):
        return False
    if _waiting(events, role, operation_id, attempt_id, operation.get("task_id"), closed):
        return None
    if (closed.type != "provider.turn.closed"
            or closed.payload.get("provider_status") != "completed"):
        return False
    claims = [e for e in events if e.type == "worker.continuation.claimed"
              and e.origin == "kernel" and e.actor == role
              and e.payload.get("recovery_action") == "reader_progress_continuation"
              and e.payload.get("operation_id") == operation_id
              and e.payload.get("request_hash") == request_hash
              and e.payload.get("attempt_id") == attempt_id]
    if claims:
        last = claims[-1]
        receipts = [e for e in events if e.type == "worker.continuation.reconciled"
                    and e.origin == "kernel" and e.actor == role
                    and e.payload.get("claim_id") == last.id
                    and e.payload.get("delivery_status") == "delivered"]
        if not receipts:
            deliveries = [e for e in events if e.type == "fanout.child.dispatched"
                          and e.origin == "kernel"
                          and e.payload.get("recovery_decision_event_id") == last.id
                          and e.payload.get("role_instance") == role
                          and e.payload.get("operation_id") == operation_id
                          and (e.payload.get("payload") or {}).get("request_hash") == request_hash
                          and (e.payload.get("payload") or {}).get("attempt_id") == attempt_id]
            if len(deliveries) != 1:
                return None
            runtime.event_writer.append(ZfEvent(
                type="worker.continuation.reconciled", origin="kernel", actor=role,
                task_id=operation.get("task_id") or None, causation_id=last.id,
                correlation_id=operation.get("workflow_run_id"),
                payload={**last.payload, "claim_id": last.id,
                         "delivery_event_id": deliveries[0].id,
                         "delivery_status": "delivered", "next_action": "observe_bound_progress"},
            ))
    progress = required_read_progress(runtime.state_dir, events,
                                     attempt_id=attempt_id, operation_id=operation_id)
    previous = claims[-1].payload.get("input_read_progress") if claims else 0
    if progress is None or type(previous) is not int or progress <= previous:
        return False
    claim = runtime.event_writer.append(ZfEvent(
        type="worker.continuation.claimed", origin="kernel", actor=role,
        task_id=operation.get("task_id") or None,
        causation_id=closed.id, correlation_id=operation.get("workflow_run_id"),
        payload={"recovery_action": "reader_progress_continuation",
                 "operation_id": operation_id, "request_hash": request_hash,
                 "attempt_id": attempt_id, "dispatch_id": p.get("run_id"),
                 "workflow_run_id": operation.get("workflow_run_id"),
                 "fanout_id": p.get("fanout_id"), "child_id": p.get("child_id"),
                 "input_read_progress": progress, "closed_event_id": closed.id,
                 "delivery_status": "unknown", "business_completed": False,
                 "recovery_owner": "run_manager",
                 "next_action": "reconcile_existing_turn_before_retry"},
    ))
    runtime._set_worker_state(role, "idle", reason="reader_input_progress", force=True)
    delivered = runtime._retry_fanout_child(
        manifest=manifest, child=child, previous_dispatch=dispatch,
        attempt=attempt, provider_session_replaced=True,
        recovery_decision_event=claim,
    )
    runtime.event_writer.append(ZfEvent(
        type="worker.continuation.reconciled", origin="kernel", actor=role,
        task_id=operation.get("task_id") or None, causation_id=claim.id,
        correlation_id=operation.get("workflow_run_id"),
        payload={**claim.payload, "claim_id": claim.id,
                 "delivery_status": "delivered" if delivered else "unknown",
                 "next_action": "observe_bound_progress" if delivered
                 else "reconcile_existing_turn_before_retry"},
    ))
    return True


def _waiting(events, role, operation_id, attempt_id, task_id, closed) -> bool:
    if closed.payload.get("pending_tool_ids") or closed.payload.get("tool_identity_unknown"):
        return True
    pending = set()
    seen_dispatch = False
    for event in events:
        p = event.payload
        if (event.type == "fanout.child.dispatched" and event.origin == "kernel"
                and p.get("operation_id") == operation_id):
            seen_dispatch = True
        if not seen_dispatch or event.actor != role:
            continue
        if event.type == "agent.tool.use":
            if not p.get("tool_use_id"):
                return True
            pending.add(p["tool_use_id"])
        elif event.type == "agent.tool.result":
            pending.discard(p.get("tool_use_id"))
    approvals = {e.payload.get("approval_ref") for e in events
                 if e.type == "approval.resolved" and e.origin == "kernel"}
    if task_id and any(
        e.type == "approval.requested" and (e.task_id or e.payload.get("task_id")) == task_id
        and e.payload.get("approval_ref") not in approvals for e in events
    ):
        return True
    resolved = {e.payload.get("request_id") for e in events
                if e.type == "worker.assistance.closed" and e.origin == "kernel"}
    return bool(pending) or any(
        e.type == "worker.assistance.requested" and e.actor == role
        and e.payload.get("operation_id") == operation_id
        and e.payload.get("active_attempt_id") == attempt_id
        and e.payload.get("request_id") not in resolved for e in events
    )
