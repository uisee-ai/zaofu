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
    amendment = pending_synth_budget_amendment(events, operation)
    if amendment is None:
        # Ordinary stop/restart belongs to reader recovery. Keep this gate only
        # when an owner budget redrive has not yet reached a successful start;
        # a revoked amendment must never fall through to ordinary recovery.
        for event in reversed(events[:redrive_index + 1]):
            if event.payload.get("operation_id") != operation_id:
                continue
            if event.type == "workflow.operation.started":
                break
            if (event.type == "workflow.operation.redrive_admitted"
                    and event.payload.get("recovery_decision_owner") == "operator_budget_amendment"):
                return False
        return None
    fanout_id = str(payload.get("fanout_id") or "")
    if runtime._fanout_identity_stale_reason(fanout_id)[0]:
        return False
    if runtime._fanout_dispatch_has_authoritative_result(events, dispatch):
        return False
    manifest = runtime._fanout_manifest(fanout_id)
    if not _carrier_recoverable(manifest, dispatch, events):
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
    if (manifest.get("aggregate") or {}).get("status") == "cancelled":
        runtime.event_writer.append(
            ZfEvent(
                type="fanout.aggregate.started",
                actor="zf-cli",
                origin="kernel",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": prepared.workflow_run_id,
                    "stage_id": payload["stage_id"],
                    "mode": (manifest.get("aggregate_config") or {}).get("mode", ""),
                    "recovery_kind": "operator_budget_amendment",
                    "recovered_from_event_id": manifest["aggregate"]["last_event_id"],
                },
                causation_id=redrive.id,
                correlation_id=prepared.workflow_run_id,
            )
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


def pending_reader_budget_amendment(
    events: list[ZfEvent], operation: dict[str, Any]
) -> ZfEvent | None:
    """Follow only an admitted reader budget redrive interrupted before transport start."""
    if operation.get("operation_type") not in {"fanout_synth", "fanout_reader_child"}:
        return None
    original_events = events
    operation_id = operation.get("operation_id")
    while operation.get("status") in {"requested", "suspended"}:
        index = next(
            (i for i, e in enumerate(events) if e.id == operation.get("last_event_id")),
            -1,
        )
        if index < 0:
            return None
        event = events[index]
        before = events[:index]
        previous = reduce_workflow_operations(before).get(operation_id, {})
        if operation.get("status") == "suspended":
            if (
                event.type != "workflow.operation.interrupted"
                or operation.get("reason") not in {"graceful_stop", "fast_stop"}
                or previous.get("status") != "requested"
            ):
                return None
        elif (
            event.type != "workflow.operation.redrive_admitted"
            or event.origin != "kernel"
            or event.payload.get("recovery_decision_owner")
            not in {"operator_budget_amendment", "kernel_replay"}
        ):
            return None
        elif previous.get("status") == "blocked":
            amendment = find_task_pipeline_budget_amendment(before, previous)
            if (
                amendment is None
                or amendment.id != event.payload.get("recovery_decision_event_id")
                or event.payload.get("recovery_decision_owner")
                != "operator_budget_amendment"
            ):
                return None
        elif previous.get("status") != "suspended":
            return None
        events, operation = before, previous
    return find_task_pipeline_budget_amendment(original_events, operation)


def pending_synth_budget_amendment(
    events: list[ZfEvent], operation: dict[str, Any]
) -> ZfEvent | None:
    if operation.get("operation_type") != "fanout_synth":
        return None
    return pending_reader_budget_amendment(events, operation)


def _carrier_recoverable(
    manifest: dict[str, Any], dispatch: ZfEvent, events: list[ZfEvent]
) -> bool:
    if not manifest or manifest.get("topology") != "fanout_reader":
        return False
    aggregate = manifest.get("aggregate") or {}
    if aggregate.get("status") in {"completed", "failed", "timed_out"}:
        return False
    by_id = {e.id: e for e in events}
    if aggregate.get("status") == "cancelled":
        from zf.runtime.reader_fanout_retry import blocked_reader_aggregate_recoverable

        cancellation = by_id.get(aggregate.get("last_event_id"))
        if (
            not blocked_reader_aggregate_recoverable(manifest)
            or cancellation is None
            or cancellation.origin != "kernel"
            or cancellation.actor != "zf-cli"
        ):
            return False
    for event in events:
        if event.type != "fanout.synth.completed" or event.payload.get(
            "fanout_id"
        ) != dispatch.payload.get("fanout_id"):
            continue
        p = event.payload
        cause = by_id.get(event.causation_id)
        if (
            event.origin != "kernel"
            or event.actor != "zf-cli"
            or p.get("failure_class") != "worker_noop_or_terminal_missing"
            or p.get("reason")
            not in {
                "provider_turn_closed_without_synth_result_recovery_exhausted",
                "reader_synth_session_replaced_after_dispatch_recovery_exhausted",
            }
            or p.get("report")
            or p.get("status") != "failed"
            or cause is None
            or cause.type != "fanout.child.dispatch_lost"
            or cause.origin != "kernel"
            or cause.actor != "zf-cli"
            or cause.payload.get("child_id") != "synth"
            or cause.payload.get("semantic_attempt_consumed") is not False
            or any(
                p.get(k) != dispatch.payload.get(k) or cause.payload.get(k) != p.get(k)
                for k in ("fanout_id", "run_id", "role_instance")
            )
        ):
            return False
    return True


def recover_pending_budget_synths(runtime: Any, events: list[ZfEvent]) -> bool:
    """Run before terminal carrier/result replay, which cannot reopen budget stops."""
    operations = reduce_workflow_operations(events)
    latest = {
        e.payload.get("operation_id"): e
        for e in events
        if e.type == "fanout.synth.dispatched"
    }
    for operation_id, operation in operations.items():
        if (
            operation.get("operation_type") != "fanout_synth"
            or operation.get("status") != "requested"
        ):
            continue
        dispatch = latest.get(operation_id)
        if dispatch and recover_budget_amended_synth(runtime, dispatch, events):
            return True
    return False
