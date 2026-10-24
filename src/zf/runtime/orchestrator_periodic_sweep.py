"""Ordered periodic housekeeping groups for the kernel Orchestrator."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent


def run_replay_sweep(
    runtime: Any,
    *,
    events: Iterable[ZfEvent] | None,
) -> None:
    event_batch = list(events or [])
    if not event_batch:
        from zf.runtime.channel_prd_evaluation_reactor import reconcile_channel_prd_evaluations

        runtime._safe_housekeeping(
            "channel_prd_evaluations", lambda: reconcile_channel_prd_evaluations(runtime),
        )
    if not event_batch or any(
        event.type in {
            "task_map.ready",
            "product.delivery.wave.ready",
            "candidate.ready",
        }
        for event in event_batch
    ):
        runtime._safe_housekeeping(
            "writer_fanout_task_bindings",
            runtime._recover_writer_fanout_task_bindings,
        )
    provider_turn_closed = any(
        event.type == "provider.turn.closed" for event in event_batch
    )
    if event_batch and not provider_turn_closed:
        return
    if not event_batch:
        runtime._safe_housekeeping(
            "writer_fanout_result_replay",
            runtime._recover_unrecorded_writer_fanout_results,
        )
        runtime._safe_housekeeping(
            "reader_fanout_trigger_replay",
            runtime._reconcile_reader_fanout_triggers,
        )
    runtime._safe_housekeeping(
        "reader_fanout_result_replay",
        lambda: _recover_reader_results_and_goal(runtime),
    )


def _recover_reader_results_and_goal(runtime: Any) -> None:
    runtime._recover_unrecorded_reader_fanout_results()
    runtime._reconcile_run_goal_completion()


def run_context_sweep(runtime: Any) -> None:
    runtime._safe_housekeeping(
        "context_thresholds",
        runtime._check_context_thresholds,
    )
    runtime._safe_housekeeping(
        "pending_recycles",
        lambda: _recover_provider_lifecycle(runtime),
    )


def _recover_provider_lifecycle(runtime: Any) -> None:
    """Run existing recycle recovery plus taskless OA operation liveness."""

    try:
        runtime._check_pending_recycles()
    finally:
        from zf.runtime.orchestrator_agent_recovery import (
            reconcile_orchestrator_agent_operation_liveness,
        )

        reconcile_orchestrator_agent_operation_liveness(runtime)


def run_post_dispatch_sweep(
    runtime: Any,
    *,
    events: Iterable[ZfEvent] | None,
    periodic_sweep: bool,
) -> None:
    channel_reply_failed = any(
        event.type == "channel.agent.reply.failed" for event in (events or [])
    )
    if periodic_sweep:
        runtime._safe_housekeeping("orphaned_tasks", runtime._check_orphaned_tasks)
        runtime._safe_housekeeping(
            "unclaimed_new_tasks",
            runtime._check_unclaimed_new_tasks,
        )
        runtime._safe_housekeeping("drift", runtime._check_drift)
        runtime._safe_housekeeping("refresh", runtime._check_refresh_triggers)
    if periodic_sweep or channel_reply_failed:
        runtime._safe_housekeeping(
            "channel_reply_remediation",
            runtime._check_channel_reply_remediation,
        )
    if periodic_sweep or any(
        event.type == "candidate.ready" for event in (events or [])
    ):
        runtime._safe_housekeeping(
            "fanout_timeouts",
            runtime._check_fanout_timeouts,
        )
