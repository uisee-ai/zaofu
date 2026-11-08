"""A delta generation cannot declare a candidate ready over unfinished Run work."""

from typing import Any, Mapping

from zf.runtime.task_pipeline_contexts import CANDIDATE_FREEZE_RECEIPT_SCHEMA


def reconcile_incomplete_freeze_scope(
    runtime: Any, contexts: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    from zf.runtime.task_pipeline_terminal import _authorized_supersession_event

    events = runtime.event_log.read_all()
    blocked_runs: set[str] = set()
    for task_id, context in contexts.items():
        run_id = str(context.get("workflow_run_id") or "")
        task = runtime.task_store.get(task_id)
        if task is not None and task.status == "done":
            continue
        if (
            task is not None
            and task.status == "cancelled"
            and _authorized_supersession_event(
                events,
                task_id=task_id,
                context=context,
            )
            is not None
        ):
            continue
        blocked_runs.add(run_id)
    if not blocked_runs:
        return blocked_runs
    # Reconcile already emitted freezes using canonical occurrence bindings;
    # only the candidate fanout is stopped, never a concurrent planner.
    by_id = {e.id: e for e in events}
    cancelled = {
        str(e.payload.get("fanout_id") or "")
        for e in events
        if e.type == "fanout.cancelled"
    }
    for event in events:
        if (
            event.type != "fanout.started"
            or event.payload.get("topology") != "fanout_reader"
        ):
            continue
        p = event.payload
        fanout_id = str(p.get("fanout_id") or "")
        if not fanout_id or fanout_id in cancelled:
            continue
        trigger = by_id.get(str(p.get("trigger_event_id") or ""))
        if trigger is None or trigger.type != "candidate.ready":
            continue
        receipt = trigger.payload
        if receipt.get("schema_version") != CANDIDATE_FREEZE_RECEIPT_SCHEMA:
            continue
        if str(receipt.get("workflow_run_id") or "") not in blocked_runs:
            continue
        manifest = runtime._fanout_manifest(fanout_id)
        if not manifest or manifest.get("status") in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }:
            continue
        runtime._cancel_superseded_fanout_manifest(
            fanout_id=fanout_id,
            manifest=manifest,
            reason="candidate_freeze_incomplete_run_scope",
            superseded_by="",
            source="task_pipeline_freeze_scope_reconciliation",
        )
    return blocked_runs
