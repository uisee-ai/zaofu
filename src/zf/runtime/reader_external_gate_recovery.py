"""Recover only an expired Reader retry whose unsent outcome is independently bound."""

from __future__ import annotations

from zf.core.events import ZfEvent
from zf.core.state.task_attempts import task_attempt_id
from zf.runtime.provider_dispatch_wait import latest_reader_gate
from zf.runtime.reader_respawn_continuation import transport_retry_input_attempt
from zf.runtime.task_attempt_runtime import task_attempt_store


def expired_unsent_retry(runtime, events: list[ZfEvent], *, failure: ZfEvent,
                         dispatch: ZfEvent, operation: dict, loss: ZfEvent | None) -> int:
    """Return the authorized successor ordinal suffix, without reviving the expired lease."""
    policy = runtime.config.workflow.task_attempt
    if not failure or not loss or policy.mode != "enforce":
        return 0
    original = dispatch.payload
    failed = failure.payload
    previous_id = original.get("run_id")
    attempt = failed.get("attempt")
    if (dispatch.origin != "kernel" or dispatch.actor != "zf-cli"
            or failure.origin != "kernel" or failure.actor != "zf-cli" or failure.causation_id != dispatch.id
            or failed.get("control_result_ref") or failed.get("semantic_submit_admission_event_id")
            or type(attempt) is not int or attempt < 2 or not previous_id
            or failed.get("retry_of_run_id") != previous_id or not original.get("task_id")
            or not all(failed.get(k) == original.get(k) for k in (
                "trace_id", "stage_id", "fanout_id", "child_id", "role_instance", "task_id"))):
        return 0
    fanout, child = original["fanout_id"], original["child_id"]
    retry = f"run-{fanout}-{child}-retry-{attempt - 1}"
    if (failed.get("run_id") != retry or operation.get("status") != "running"
            or operation.get("operation_type") != "fanout_reader_child"
            or operation.get("call_result_admitted_event_id")
            or operation.get("role_instance") != original["role_instance"]
            or loss.origin != "kernel" or loss.actor != "zf-cli"
            or any(loss.payload.get(k) != original.get(k) for k in ("fanout_id", "child_id", "role_instance", "run_id"))
            or loss.correlation_id != dispatch.correlation_id
            or not loss.causation_id or loss.payload.get("lost_signal_event_id") != loss.causation_id):
        return 0
    input_attempt = transport_retry_input_attempt(runtime, dispatch,
        operation_id=operation["operation_id"], role=original["role_instance"])
    if not input_attempt or input_attempt != operation.get("active_attempt_id"):
        return 0
    store = task_attempt_store(runtime)
    row_id = task_attempt_id(operation["workflow_run_id"], original["task_id"], retry)
    row = store.get(row_id) or {}
    current = store.current_for_attempt(task_id=original["task_id"], attempt_id=row_id) or {}
    successor_id = task_attempt_id(operation["workflow_run_id"], original["task_id"],
                                   f"run-{fanout}-{child}-retry-{attempt}")
    if (row.get("retryable") is not True or row.get("operation_id") != operation["operation_id"] or row.get("sent_at")
            or row.get("delivery_claimed_at") or row.get("failure_class") != "lease_expired"
            or current.get("attempt_id") not in {row_id, successor_id}
            or not (row.get("status") == "expired" and current.get("attempt_id") == row_id
                    or row.get("superseded_from_status") == "expired" and row.get("superseded_by") == successor_id
                    and current.get("status") == "prepared" and current.get("unsent_external_gate"))):
        return 0
    # Old stores removed the claim timestamp only after transport confirmed zero
    # input. Require the bound gate, prior claim and scheduler's finite retry grant.
    identity = {"operation_id": operation["operation_id"], "workflow_run_id": operation["workflow_run_id"],
                "task_id": original["task_id"], "attempt_id": row_id, "lease_id": row["lease_id"],
                "dispatch_id": retry}
    scope = [e for e in events if e.origin == "kernel" and e.actor == "orchestrator"
             and all(e.payload.get(k) == v for k, v in identity.items())]
    claim = next((e for e in scope if e.type == "task.attempt.delivery_claimed"), None)
    grant = next((e for e in scope if e.type == "task.attempt.retry_scheduled"
                  and e.payload.get("reason") == "lease_expired"
                  and e.payload.get("next_ordinal") == row.get("ordinal", 0) + 1
                  and e.payload["next_ordinal"] <= min(policy.max_attempts, e.payload.get("max_attempts", 0))), None)
    closed = next((e for e in events if e.id == loss.causation_id), None)
    if (not closed or closed.type != "provider.turn.closed"
            or closed.actor != original["role_instance"] or closed.payload.get("provider_status") != "failed"
            or not closed.payload.get("turn_id") or closed.payload.get("provider_stop_reason") not in {
                "quota_exhausted", "auth_error", "tool_permission_blocked", "rate_limited"}):
        return 0
    if not claim or not grant or any(e.type == "task.attempt.sent" for e in scope):
        return 0
    positions = {e.id: i for i, e in enumerate(events)}
    gate = latest_reader_gate(events[:positions[failure.id]], fanout_id=fanout,
                              child_id=child, dispatch_id=retry)
    if (not gate or gate.causation_id != dispatch.id
            or gate.payload.get("reason") not in {
                "provider_interactive:usage_limit_reached", "provider_interactive:quota_model_switch",
                "provider_interactive:login_required", "provider_interactive:trust_prompt",
                "provider_interactive:permission_confirmation"}
            or gate.payload.get("role_instance") != original["role_instance"]
            or not (positions[dispatch.id] < positions[closed.id] < positions[loss.id] < positions[claim.id]
                    < positions[gate.id] < positions[grant.id] < positions[failure.id])):
        return 0
    return attempt
