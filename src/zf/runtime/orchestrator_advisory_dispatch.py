"""Dispatch advisory turns, including durable read-only worker guidance."""
from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent


def dispatch_advisory_turn(
    runtime, orch_role: RoleConfig, triggers: list[ZfEvent]
) -> None:
    """Send one Layer 2 turn covering `triggers` (most recent is primary)."""
    from zf.runtime.orchestrator import (
        regenerate_progress, build_orchestrator_briefing, transport_error_diagnostics,
    )
    if not triggers:
        return
    from zf.runtime.orchestrator_agent_transport import orchestrator_checkpoint_busy

    primary = triggers[-1]
    if primary.type == "orchestrator.semantic.checkpoint.requested":
        from zf.runtime.orchestrator_agent_transport import (
            dispatch_orchestrator_agent_operation,
        )

        dispatch_orchestrator_agent_operation(runtime, orch_role, primary)
        return
    if orchestrator_checkpoint_busy(runtime):
        for trigger in triggers:
            runtime._add_layer2_pending(trigger)
        return
    from zf.runtime.worker_assistance import claim_assistance_dispatch

    if not claim_assistance_dispatch(runtime, primary):
        return
    # Regenerate progress.md so Layer 2 sees fresh narrative state.
    try:
        regenerate_progress(runtime.state_dir)
    except Exception:
        pass  # progress.md is optional; never block dispatch on it
    also_triggered = [e.type for e in triggers[:-1]]
    briefing = build_orchestrator_briefing(
        state_dir=runtime.state_dir,
        config=runtime.config,
        trigger_event=primary,
        also_triggered=also_triggered,
    )
    briefings_dir = runtime.state_dir / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    ts_safe = primary.ts.replace(":", "-").replace(".", "-")
    evid_short = primary.id.replace("evt-", "")[:12]
    briefing_path = briefings_dir / f"orchestrator-{ts_safe}-{evid_short}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    extra = f" (+{len(also_triggered)} coalesced)" if also_triggered else ""
    prompt = (
        f"You are the orchestrator agent. New events have fired{extra} and you "
        f"need to decide what to do next.\n"
        f"Read the briefing at {briefing_path} for the full system state and trigger details. "
        f"Make ONE round of decisions via tool calls, then exit."
    )
    try:
        runtime._record_skill_provenance(role=orch_role, task_id=primary.task_id)
        context = runtime._dispatch_context(
            role=orch_role,
            briefing_path=briefing_path,
            task_id=primary.task_id,
            trace_id=primary.correlation_id,
        )
        from zf.runtime.orchestrator_agent_role import orchestrator_instance_id

        runtime._send_transport_task(
            orchestrator_instance_id(runtime), briefing_path, prompt, context
        )
    except Exception as exc:
        payload = {
            "trigger_event_id": primary.id,
            "error": str(exc),
        }
        payload.update(transport_error_diagnostics(exc))
        runtime.event_writer.append(ZfEvent(
            type="orchestrator.dispatch_failed",
            actor="zf-cli",
            payload=payload,
        ))
        if str(payload.get("dead_reason") or "") == "pane_dead":
            runtime.event_writer.append(ZfEvent(
                type="worker.respawn.requested",
                actor="zf-cli",
                payload={
                    "role": "orchestrator",
                    "instance_id": "orchestrator",
                    "trigger_event_id": primary.id,
                    "reason": "pane_dead_dispatch_failed",
                    "source_event_type": "orchestrator.dispatch_failed",
                    "source": "dispatch_failure_recovery",
                },
                causation_id=primary.id,
                correlation_id=primary.correlation_id,
            ))
            runtime.event_writer.append(ZfEvent(
                type="orchestrator.dispatch.retry_requested",
                actor="zf-cli",
                payload={
                    "role": "orchestrator",
                    "assignee": "orchestrator",
                    "trigger_event_id": primary.id,
                    "reason": "retry_after_pane_respawn",
                    "source": "dispatch_failure_recovery",
                    "max_attempts": 1,
                },
                causation_id=primary.id,
                correlation_id=primary.correlation_id,
            ))
