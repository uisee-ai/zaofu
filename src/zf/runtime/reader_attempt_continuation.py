"""Continue a progressing Reader without allocating a new TaskAttempt."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.provider_turn_liveness import _parse_event_ts
from zf.runtime.reader_recovery_owner import another_reader_owns_role
from zf.runtime.reader_respawn_continuation import reader_continuation_guidance
from zf.runtime.task_attempt_runtime import task_attempt_store
from zf.runtime.transport import DispatchContext
from zf.runtime.workflow_operation import reduce_workflow_operations


def validate_reader_continuation(
    runtime: Any, role: str, context: DispatchContext,
    events: list[ZfEvent], claim: ZfEvent,
) -> None:
    """Bind the durable call identity and the separate transport attempt identity."""
    p = claim.payload
    dispatches = [e for e in events if e.type == "fanout.child.dispatched"
                  and e.payload.get("operation_id") == context.operation_id]
    dispatch = dispatches[-1] if dispatches else None
    operation = reduce_workflow_operations(events).get(context.operation_id, {})
    if (claim.origin != "kernel" or claim.actor != role or dispatch is None
            or operation.get("status") != "running"
            or operation.get("role_instance") != role
            or operation.get("workflow_run_id") != context.run_id
            or operation.get("task_id") != context.task_id
            or operation.get("request_hash") != p.get("request_hash")
            or operation.get("active_attempt_id") != p.get("attempt_id")
            or p.get("operation_id") != context.operation_id
            or p.get("dispatch_id") != context.dispatch_id
            or p.get("fanout_id") != dispatch.payload.get("fanout_id")
            or p.get("child_id") != dispatch.payload.get("child_id")
            or dispatch.payload.get("run_id") != context.dispatch_id
            or dispatch.payload.get("role_instance") != role
            or dispatch.payload.get("recovery_decision_event_id") == claim.id
            or another_reader_owns_role(events, dispatch)):
        raise RuntimeError("Reader continuation identity is stale")
    nested = dispatch.payload.get("payload") or {}
    if (nested.get("attempt_id") != p.get("attempt_id")
            or nested.get("request_hash") != p.get("request_hash")):
        raise RuntimeError("Reader continuation call binding diverged")
    expected = {"run_id": context.run_id, "task_id": context.task_id,
                "operation_id": context.operation_id, "instance_id": role,
                "dispatch_id": context.dispatch_id, "attempt_id": context.attempt_id,
                "lease_id": context.lease_id}
    rows = [r for r in task_attempt_store(runtime).current_rows()
            if r.get("attempt_id") == context.attempt_id]
    row = rows[0] if len(rows) == 1 else {}
    if row.get("status") != "sent" or any(not v or row.get(k) != v for k, v in expected.items()):
        raise RuntimeError("Reader continuation requires the current sent TaskAttempt")
    expiry = _parse_event_ts(row.get("lease_expires_at"))
    if not expiry or expiry <= datetime.now(timezone.utc):
        raise RuntimeError("Reader continuation lease expired")
    if runtime._fanout_identity_stale_reason(str(p.get("fanout_id") or ""))[0]:
        raise RuntimeError("Reader continuation fanout is stale")
    after = events[events.index(dispatch) + 1:]
    if any(e.type in {"fanout.child.completed", "fanout.child.failed"}
           and e.payload.get("fanout_id") == p.get("fanout_id")
           and e.payload.get("child_id") == p.get("child_id") for e in after):
        raise RuntimeError("Reader continuation child is terminal")
    if any(e.type == "worker.continuation.reconciled"
           and e.payload.get("claim_id") == claim.id for e in events):
        raise RuntimeError("Reader continuation claim was already reconciled")
    if not runtime.transport.is_alive(role):
        raise RuntimeError("Reader continuation requires the existing live worker")


def deliver_reader_continuation(runtime: Any, dispatch: ZfEvent, claim: ZfEvent) -> bool:
    """Use the charging/permission gate and append delivery only after send returns."""
    from zf.runtime.injection import build_task_prompt
    from zf.runtime.cli_command import zf_cli_cmd

    p = dispatch.payload
    role = runtime._find_role_by_instance(str(p.get("role_instance") or ""))
    rows = [r for r in task_attempt_store(runtime).current_rows()
            if r.get("operation_id") == p.get("operation_id")
            and r.get("dispatch_id") == p.get("run_id")
            and r.get("instance_id") == p.get("role_instance")]
    if role is None or len(rows) != 1:
        raise RuntimeError("Reader continuation has no unique current attempt")
    row = rows[0]
    briefing = Path(str(p.get("briefing_path") or row.get("briefing_ref") or ""))
    if not briefing.is_file():
        raise RuntimeError("Reader continuation briefing is missing")
    context = DispatchContext(
        trace_id=dispatch.correlation_id, run_id=row["run_id"], task_id=row["task_id"],
        role_name=role.name, instance_id=role.instance_id, backend=role.backend,
        briefing_path=briefing, dispatch_id=row["dispatch_id"],
        operation_id=row["operation_id"], attempt_id=row["attempt_id"],
        lease_id=row["lease_id"], continuation=True, continuation_id=claim.id,
    )
    prompt = build_task_prompt(role.instance_id, briefing, prompt_kind="fanout_child")
    prompt += reader_continuation_guidance(
        attempt_id=claim.payload["attempt_id"], backend=role.backend, cli=zf_cli_cmd(),
    )
    if claim.payload.get("continuation_phase") == "result_completion":
        prompt += (
            " This is the single bounded result-completion continuation for this call. "
            "Required-read coverage is complete; finish the remaining independent evaluation, "
            "update the current result scratch and use the briefing's validated submission. "
            "Coverage alone is not understanding or acceptance: reread anything no longer "
            "available in this session and preserve every existing evidence and permission gate."
        )
    runtime._send_transport_task(role.instance_id, briefing, prompt, context)
    runtime._note_prompt_sent(role.instance_id, row["dispatch_id"])
    runtime.event_writer.append(ZfEvent(
        type="fanout.child.dispatched", origin="kernel", actor="zf-cli",
        task_id=row["task_id"], correlation_id=dispatch.correlation_id,
        causation_id=claim.id,
        payload={**p, "recovery_decision_event_id": claim.id, "continuation_id": claim.id},
    ))
    runtime._set_worker_state(role.instance_id, "busy",
                              reason="reader_result_completion" if claim.payload.get("continuation_phase") == "result_completion"
                              else "reader_input_progress",
                              task_id=row["task_id"])
    return True
