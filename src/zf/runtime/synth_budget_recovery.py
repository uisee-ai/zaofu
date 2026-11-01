"""Replay an owner-amended synth call using its original immutable request."""

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_runtime import (
    PreparedCallOperation,
    mark_call_operation_started,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.injection import build_task_prompt
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.workflow_operation import (
    operation_request_hash,
    reduce_workflow_operations,
)
from zf.runtime.workflow_operation_task_pipeline import (
    find_task_pipeline_budget_amendment,
)


def recover_budget_amended_synth(
    runtime: Any, dispatch: ZfEvent, events: list[ZfEvent]
) -> bool | None:
    """Return None for ordinary recovery, False for a pending budget gate."""
    payload = dispatch.payload or {}
    operation_id = str(payload.get("operation_id") or "")
    operation = reduce_workflow_operations(events).get(operation_id, {})
    if operation.get("operation_type") != "fanout_synth":
        return None
    if operation.get("status") == "blocked":
        return False
    if operation.get("status") != "requested":
        return None
    redrive_index = next(
        (i for i, e in enumerate(events) if e.id == operation.get("last_event_id")), -1
    )
    if redrive_index < 0:
        return None
    redrive = events[redrive_index]
    if (
        redrive.type != "workflow.operation.redrive_admitted"
        or redrive.origin != "kernel"
        or redrive.payload.get("recovery_decision_owner") != "operator_budget_amendment"
    ):
        return None
    before = events[:redrive_index]
    old = reduce_workflow_operations(before).get(operation_id, {})
    amendment = find_task_pipeline_budget_amendment(before, old)
    if amendment is None or amendment.id != redrive.payload.get(
        "recovery_decision_event_id"
    ):
        return False
    body = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload
    if operation_request_hash(body) != operation.get("request_hash"):
        return False
    request = body["request"]
    if any(
        str(payload.get(k) or "") != str(operation.get(k) or "")
        for k in (
            "request_hash",
            "workflow_run_id",
            "role_instance",
        )
    ):
        return False
    run_id = str(payload.get("run_id") or "")
    if not run_id or run_id != str(operation.get("active_attempt_id") or ""):
        return False
    role = next(iter(runtime._fanout_roles([operation["role_instance"]])), None)
    briefing = Path(str(payload.get("briefing_path") or ""))
    if role is None or not briefing.is_file():
        return False
    runtime._set_worker_state(
        role.instance_id, "idle", reason="owner_budget_amended", force=True
    )
    if not runtime._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id=payload["fanout_id"],
        stage_id=payload["stage_id"],
        child_id="synth",
        run_id=run_id,
        trace_id=operation["workflow_run_id"],
        causation_id=redrive.id,
        prompt_kind="fanout_synth",
        skip_send_window=True,
        provider_session_replaced=True,
    ):
        return False
    prepared = PreparedCallOperation(
        mode=str(payload.get("result_protocol_mode") or "blocking"),
        workflow_run_id=operation["workflow_run_id"],
        operation_id=operation_id,
        request_hash=operation["request_hash"],
        attempt_id=operation["active_attempt_id"],
        lease_id=str(operation.get("lease_id") or run_id),
        role_instance=role.instance_id,
        task_id=str(operation.get("task_id") or ""),
        parent_task_id=str(operation.get("parent_task_id") or ""),
        output_profile_id=str(request.get("output_profile_id") or ""),
        output_profile_revision=str(request.get("output_profile_revision") or ""),
        result_scratch_ref=str(request.get("result_scratch_ref") or ""),
        should_dispatch=True,
        ensure_status="requested",
    )
    context = runtime._dispatch_context(
        role=role,
        briefing_path=briefing,
        trace_id=prepared.workflow_run_id,
        task_id=prepared.task_id,
        parent_task_id=prepared.parent_task_id,
        run_id=prepared.workflow_run_id,
        operation_id=operation_id,
        dispatch_id=run_id,
    )
    cli = zf_cli_cmd()
    prompt = build_task_prompt(role.instance_id, briefing, prompt_kind="fanout_synth")
    prompt += (
        f"\nOwner budget continuation: {amendment.id}. Current runtime CLI: `{cli}`. "
        "Use this CLI with the same arguments instead of any older executable paths "
        "in the original briefing. Request identity and required reads remain unchanged."
    )
    runtime._send_transport_task(role.instance_id, briefing, prompt, context)
    mark_call_operation_started(
        runtime,
        prepared,
        task_id=prepared.task_id,
        dispatch_id=run_id,
        causation_id=redrive.id,
        correlation_id=prepared.workflow_run_id,
    )
    runtime._note_prompt_sent(role.instance_id, run_id)
    runtime.event_writer.append(
        ZfEvent(
            type="fanout.synth.dispatched",
            actor="zf-cli",
            origin="kernel",
            task_id=prepared.task_id or None,
            payload={
                **payload,
                "retry_of_event_id": dispatch.id,
                "recovery_kind": "operator_budget_amendment",
                "recovery_cli_cmd": cli,
                "semantic_attempt_consumed": False,
            },
            causation_id=redrive.id,
            correlation_id=prepared.workflow_run_id,
        )
    )
    return True
