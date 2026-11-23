"""Release superseded Reader owners without rewriting terminal fanout results."""
from typing import Any


def retire_superseded_terminal_readers(runtime: Any, events: list, manifests: list,
                                      *, recovery_snapshot=None) -> bool:
    from zf.runtime.reader_fanout_recovery_snapshot import event_log_snapshot_token
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operations = reduce_workflow_operations(events)
    by_id = {e.id: e for e in events}
    started_order = {str(e.payload.get("fanout_id") or ""): i for i, e in enumerate(events)
                     if e.type == "fanout.started"}
    changed = False
    for fanout_id, manifest in manifests:
        if not manifest or manifest.get("topology") != "fanout_reader":
            continue
        terminal = {"completed", "failed", "timed_out", "cancelled"}
        if (manifest.get("status") not in terminal
                and (manifest.get("aggregate") or {}).get("status") not in terminal):
            continue
        run_id = (manifest.get("workflow_run_id")
                  or (manifest.get("trigger_payload") or {}).get("workflow_run_id")
                  or manifest.get("trace_id"))
        selected = []
        children = list(manifest.get("children") or [])
        if isinstance(manifest.get("synth"), dict):
            children.append(manifest["synth"])
        for child in children:
            op_id = child.get("operation_id") or (child.get("payload") or {}).get("operation_id")
            op = operations.get(op_id, {})
            if (op.get("status") in {"requested", "reserved", "running", "suspended"}
                    and op.get("workflow_run_id") == run_id
                    and op.get("parent_stage_id") == manifest.get("stage_id")):
                selected.append(child)
        if not selected:
            continue
        reason, superseded_by = runtime._reader_fanout_stale_status(
            fanout_id=fanout_id, manifest=manifest, manifests=manifests,
            started_order=started_order, events=events,
            recovery_snapshot=(recovery_snapshot if recovery_snapshot is not None
                               and fanout_id in recovery_snapshot.currentness else None))
        proof = by_id.get(superseded_by)
        # Mere terminality, a proposed replan, or a busy new reader is no authority.
        if (not reason or proof is None or proof.origin != "kernel"
                or proof.type not in {"task.pipeline.generation.admitted", "task_map.admitted"}
                or (proof.payload.get("workflow_run_id") or proof.correlation_id) != run_id):
            continue
        before = event_log_snapshot_token(runtime.event_log)
        old = {**manifest, "children": selected}
        old.pop("synth", None)
        runtime._cancel_superseded_reader_fanout_operations(
            manifest=old, reason=reason, cancellation_event=proof)
        changed = changed or event_log_snapshot_token(runtime.event_log) != before
    return changed
