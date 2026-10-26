"""Transport edge for one durable Orchestrator Agent operation."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.orchestrator_agent_briefing import (
    build_orchestrator_agent_operation_briefing,
)
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
    fail_orchestrator_agent_operation,
    interrupt_orchestrator_agent_operation,
    prepared_operation_from_checkpoint_event,
    retry_orchestrator_agent_operation,
    workflow_operation_service,
)
from zf.runtime.orchestrator_agent_recovery import (
    request_orchestrator_agent_checkpoint_respawn,
)
from zf.runtime.transport import transport_error_diagnostics
from zf.runtime.workflow_operation import reduce_workflow_operations


def dispatch_orchestrator_agent_operation(
    runtime: Any,
    orch_role: Any,
    trigger: ZfEvent,
) -> None:
    prepared = prepared_operation_from_checkpoint_event(runtime, trigger)
    if prepared is None or orchestrator_checkpoint_busy(
        runtime, exclude_operation_id=prepared.operation_id,
    ):
        return  # Requested operation remains durable for the next free turn.
    briefing = build_orchestrator_agent_operation_briefing(
        state_dir=runtime.state_dir,
        prepared=prepared,
        cli_command=zf_cli_cmd(),
    )
    briefings_dir = runtime.state_dir / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    briefing_path = briefings_dir / f"orchestrator-{prepared.operation_id}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    prompt = (
        "Execute the typed Orchestrator Agent semantic checkpoint in "
        f"{briefing_path}. Read every required source and submit exactly "
        "one typed result; do not mutate canonical runtime state."
    )
    trigger_payload = trigger.payload if isinstance(trigger.payload, dict) else {}
    retry_attempt = int(
        trigger_payload.get("checkpoint_dispatch_retry_attempt") or 0
    )
    max_retry_attempts = int(
        trigger_payload.get("checkpoint_dispatch_retry_max_attempts") or 1
    )
    if retry_attempt:
        retry_orchestrator_agent_operation(
            runtime,
            prepared,
            retry_attempt=retry_attempt,
            dispatch_id=trigger.id,
            causation_id=trigger.id,
        )
    else:
        activate_orchestrator_agent_operation(
            runtime,
            prepared,
            dispatch_id=trigger.id,
            causation_id=trigger.id,
        )
    try:
        runtime._record_skill_provenance(role=orch_role, task_id=None)
        context = runtime._dispatch_context(
            role=orch_role,
            briefing_path=briefing_path,
            task_id=None,
            trace_id=prepared.workflow_run_id,
        )
        runtime._send_transport_task(
            "orchestrator",
            briefing_path,
            prompt,
            context,
        )
    except Exception as exc:
        payload = {
            "trigger_event_id": trigger.id,
            "operation_id": prepared.operation_id,
            "error": str(exc),
        }
        payload.update(transport_error_diagnostics(exc))
        runtime.event_writer.append(ZfEvent(
            type="orchestrator.dispatch_failed",
            actor="zf-cli",
            payload=payload,
            causation_id=trigger.id,
            correlation_id=prepared.workflow_run_id,
        ))
        pane_dead = str(payload.get("dead_reason") or "") == "pane_dead"
        if pane_dead and retry_attempt < max_retry_attempts:
            interrupt_orchestrator_agent_operation(
                runtime,
                prepared,
                reason=f"transient_transport:pane_dead:{type(exc).__name__}",
                causation_id=trigger.id,
            )
            next_attempt = retry_attempt + 1
            request_orchestrator_agent_checkpoint_respawn(
                runtime,
                operation_id=prepared.operation_id,
                request_hash=prepared.request_hash,
                workflow_run_id=prepared.workflow_run_id,
                checkpoint_event_id=trigger.id,
                trigger_event_id=trigger.id,
                causation_id=trigger.id,
                reason="pane_dead_checkpoint_dispatch_failed",
                source_event_type="orchestrator.dispatch_failed",
                retry_attempt=next_attempt,
                max_attempts=max_retry_attempts,
            )
        elif retry_attempt:
            workflow_operation_service(runtime).block(
                operation_id=prepared.operation_id,
                request_hash=prepared.request_hash,
                workflow_run_id=prepared.workflow_run_id,
                reason=(
                    "orchestrator checkpoint transport retry exhausted: "
                    f"{type(exc).__name__}: {exc}"
                ),
                causation_id=trigger.id,
                correlation_id=prepared.workflow_run_id,
            )
        else:
            fail_orchestrator_agent_operation(
                runtime,
                prepared,
                reason=f"{type(exc).__name__}: {exc}",
                causation_id=trigger.id,
            )


def orchestrator_checkpoint_busy(runtime: Any, *, exclude_operation_id: str = "") -> bool:
    return any(
        row.get("operation_id") != exclude_operation_id
        and row.get("operation_type") == "orchestrator_agent_semantic"
        and row.get("role_instance") == "orchestrator"
        and row.get("status") in {"reserved", "running", "suspended"}
        for row in reduce_workflow_operations(runtime.event_log.read_all()).values()
    )


def dispatch_pending_orchestrator_checkpoint(runtime: Any) -> None:
    """Drain one durable request, including after restart or a lost wake."""
    role = runtime._find_role_by_name("orchestrator")
    if role is None:
        return
    events = runtime.event_log.read_all()
    operations = reduce_workflow_operations(events)
    if any(row.get("operation_type") == "orchestrator_agent_semantic"
           and row.get("role_instance") == "orchestrator"
           and row.get("status") in {"reserved", "running", "suspended"}
           for row in operations.values()):
        return
    for event in events:
        if event.type != "orchestrator.semantic.checkpoint.requested":
            continue
        operation = operations.get(str(event.payload.get("operation_id") or ""), {})
        if operation.get("status") == "requested":
            runtime._notify_orchestrator_agent(event)
            return


__all__ = ["dispatch_orchestrator_agent_operation"]
