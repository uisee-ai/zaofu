"""Deliver durable semantic-submit corrections after the provider turn ends."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from zf.runtime.call_result_admission import CallResultAdmissionOutcome
from zf.runtime.call_result_correction import dispatch_call_result_correction
from zf.runtime.event_window import read_runtime_events
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events
from zf.runtime.run_scope import event_run_id, run_aliases
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.terminal_events import latest_quiescent_run_terminal
from zf.runtime.workflow_operation import reduce_workflow_operations


def reconcile_call_result_repairs(runtime: Any) -> int:
    """Replay pending corrections, without reopening or settling any operation.

    Run under the existing Orchestrator loop lock. The repair request and
    dispatch receipt are the durable intent/ack; no second state store is used.
    Legacy result admission retains its existing inline correction route.
    """
    if (Path(runtime.state_dir) / "shutdown-requested").exists():
        return 0
    events = read_runtime_events(runtime.event_log, runtime.state_dir)
    if is_dispatch_paused(runtime.state_dir, events=events):
        return 0
    operations = reduce_workflow_operations(events)
    pending = {}
    dispatched = set()
    exhausted = set()
    for event in events:
        payload = event.payload
        key = (payload.get("operation_id"), payload.get("request_hash"))
        if event.type == "workflow.call.result.repair.requested":
            pending[key] = event
        elif event.type == "workflow.call.result.repair.dispatched":
            dispatched.add((*key, (payload.get("correction_ref") or {}).get("sha256")))
        elif event.type == "workflow.call.result.invalid":
            exhausted.add(key)
    aliases = run_aliases(events)
    cancelled = {event_run_id(e, aliases=aliases) for e in events if e.type == "run.cancelled"}
    paused_runs = {}
    for event in events:
        if event.type in {"run.paused", "run.resumed"}:
            paused_runs[event_run_id(event, aliases=aliases)] = event.type == "run.paused"
    count = 0
    for (operation_id, request_hash), event in pending.items():
        payload = event.payload
        if (operation_id, request_hash) in exhausted:
            continue
        correction_ref = payload.get("correction_ref") or {}
        if (operation_id, request_hash, correction_ref.get("sha256")) in dispatched:
            continue
        operation = operations.get(operation_id, {})
        if operation.get("status") != "running" or operation.get("request_hash") != request_hash:
            continue
        run_id = str(operation.get("workflow_run_id") or "")
        if (aliases.get(run_id, run_id) in cancelled
                or paused_runs.get(aliases.get(run_id, run_id), False)
                or latest_quiescent_run_terminal(events, run_id=run_id)):
            continue
        request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
        if request.get("semantic_result_submit_mode") != "blocking":
            continue
        packet = hydrate_sidecar_ref(runtime.state_dir, correction_ref).payload
        if any(str(packet.get(key) or "") != str(expected or "") for key, expected in (
            ("operation_id", operation_id), ("request_hash", request_hash),
            ("attempt_id", operation.get("active_attempt_id")),
            ("dispatch_id", operation.get("dispatch_id")),
            ("workflow_run_id", run_id),
        )):
            continue
        actor = str(operation.get("role_instance") or "")
        # A resident lane must still belong solely to this operation. Never send
        # an old correction to a role that has acquired another active operation.
        if not actor or any(other.get("role_instance") == actor
                            and other.get("status") == "running"
                            and other.get("operation_id") != operation_id
                            for other in operations.values()):
            continue
        if active_codex_turn_from_events(events, actor):
            continue
        source = replace(event, actor=actor, task_id=operation.get("task_id") or None,
                         payload={**request, "role_instance": actor,
                                  "dispatch_id": operation.get("dispatch_id", ""),
                                  "workflow_run_id": run_id})
        outcome = CallResultAdmissionOutcome(
            status="repair_pending", mode="blocking", operation_id=operation_id,
            request_hash=request_hash, correction_ref=correction_ref,
            repair_round=int(payload.get("repair_round") or 0),
            issues=tuple(packet.get("issues") or ()), correction_dispatch_required=True,
        )
        if dispatch_call_result_correction(runtime, source_event=source, outcome=outcome,
                                           stop_timeout_seconds=0.0):
            count += 1
    return count
