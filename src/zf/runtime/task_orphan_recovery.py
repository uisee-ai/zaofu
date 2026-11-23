"""Handle orphaned tasks while preserving an existing Candidate review owner."""

from zf.runtime.candidate_aggregate_review import (
    is_candidate_review_request,
    pending_candidate_reviews,
)


def orphan_allows_candidate_review(decision, action, events):
    if (
        action.get("action") != "orchestrator-rework-triage"
        or action.get("owner_route") != "run_manager"
        or not is_candidate_review_request(action)
        or not events
    ):
        return False
    run, task = action.get("workflow_run_id"), action.get("task_id")
    if not run or not task:
        return False
    # Older EscalationService records omitted origin and correlation on the
    # alert. Its immediately preceding kernel task.orphaned proves the binding.
    anchor = None
    failure = None
    previous = None
    for event in events:
        if event.id == action.get("source_event_id"):
            failure = event
        if event.id == decision.get("event_id"):
            if (
                event.type != "human.escalate"
                or event.actor != "orchestrator"
                or event.origin not in {"", "kernel"}
                or event.task_id != task
                or event.payload.get("failure_class") != "orphaned_task"
            ):
                return False
            anchor = previous
            break
        previous = event
    return bool(
        anchor
        and anchor.type == "task.orphaned"
        and anchor.origin == "kernel"
        and anchor.actor == "zf-cli"
        and anchor.task_id == task
        and anchor.correlation_id == run
        and failure
        and failure.origin == "kernel"
        and failure.type == action["source_event_type"]
        and failure.payload.get("pdd_id") == task
        and failure.payload.get("workflow_run_id") == run
        and failure.payload.get("fanout_id") == action.get("fanout_id")
    )


def orphaned_review_needs_tick(state_dir, config, project_root, events, alert):
    if not alert or alert.payload.get("failure_class") != "orphaned_task":
        return False
    from zf.runtime.run_manager import build_run_manager_projection

    gate = {"event_id": alert.id}
    candidates = pending_candidate_reviews(
        state_dir=state_dir, config=config, events=events
    )
    if not any(orphan_allows_candidate_review(gate, a, events) for a in candidates):
        return False
    projection = build_run_manager_projection(
        state_dir, events=events, config=config, project_root=project_root
    )
    selected = (projection.get("continuation") or {}).get("next_operation") or {}
    return any(
        row.get("operation_key") == selected.get("operation_key")
        and orphan_allows_candidate_review(gate, row, events)
        for row in projection["pending_actions"]
    )


def anchor_awaits_candidate_review(state_dir, config, events, task_id):
    if not any(
        e.type in {"test.failed", "verify.failed"}
        and e.origin == "kernel"
        and e.payload.get("pdd_id") == task_id
        and e.payload.get("fanout_id")
        for e in reversed(events)
    ):
        return False
    return any(
        row.get("task_id") == task_id and is_candidate_review_request(row)
        for row in pending_candidate_reviews(
            state_dir=state_dir, config=config, events=events
        )
    )


def record_task_orphaned(runtime, task, role, elapsed):
    from zf.core.events import ZfEvent

    """LH-0.T3: full orphan escalation — emit, unassign, requeue."""
    try:
        runtime.event_writer.append(
            ZfEvent(
                type="task.orphaned",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": role.name,
                    "assigned_to": task.assigned_to or "",
                    "elapsed_seconds": round(elapsed, 1),
                    "threshold_seconds": role.orphan_escalate_seconds,
                },
            )
        )
    except Exception:
        pass
    try:
        runtime.escalation.escalate(
            f"task {task.id}: orphaned "
            f"({elapsed:.0f}s > {role.orphan_escalate_seconds:.0f}s)",
            task_id=task.id,
            metadata={"failure_class": "orphaned_task"},
        )
    except Exception:
        pass
    # Requeue for future dispatch. Clearing assigned_to also frees
    # the WIP slot for that instance.
    try:
        runtime.task_store.update(
            task.id,
            status="backlog",
            assigned_to="",
        )
    except Exception:
        pass
