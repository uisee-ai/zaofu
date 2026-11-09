"""Mechanical closeout for resources owned by a cancelled workflow run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.cancelled_run_workers import stop_cancelled_dispatch
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from zf.runtime.run_scope import event_run_id, run_aliases
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)


_FANOUT_TERMINAL_STATUSES = frozenset({
    "completed",
    "failed",
    "timed_out",
    "cancelled",
    "corrected_passed",
    "closed",
})
_OPERATION_ACTIVE_STATUSES = frozenset({"requested", "reserved", "running"})


def reconcile_cancelled_run_resources(
    runtime: Any,
    *,
    trigger_event: ZfEvent | None = None,
) -> list[WorkflowRuntimeDecision]:
    """Close fanouts, operations, tasks, and worker bindings for run cancel."""

    if trigger_event is not None and trigger_event.type != "run.cancelled":
        return []
    if trigger_event is None:
        if getattr(runtime, "_run_cancel_resources_reconciled", False):
            return []

    try:
        events = list(runtime.event_log.read_all())
    except Exception:
        return []
    if trigger_event is None:
        runtime._run_cancel_resources_reconciled = True
        from zf.runtime.cancelled_run_workers import reconcile_pending_stops

        reconcile_pending_stops(runtime, events)
    if trigger_event is not None and not any(
        event.id and event.id == trigger_event.id for event in events
    ):
        events.append(trigger_event)
    cancellations = (
        [trigger_event]
        if trigger_event is not None
        else [event for event in events if event.type == "run.cancelled"]
    )
    aliases = run_aliases(events)
    decisions: list[WorkflowRuntimeDecision] = []
    for cancellation in cancellations:
        run_id = _cancelled_run_id(cancellation, aliases)
        if not run_id:
            continue
        changed = _close_cancelled_run(
            runtime,
            cancellation=cancellation,
            run_id=run_id,
            aliases=aliases,
            events=events,
        )
        if changed:
            decisions.append(WorkflowRuntimeDecision(
                action="cancel",
                task_id=str(cancellation.task_id or ""),
                reason=f"run.cancelled closed runtime resources for {run_id}",
            ))
            events = list(runtime.event_log.read_all())
            aliases = run_aliases(events)
    return decisions


def _close_cancelled_run(
    runtime: Any,
    *,
    cancellation: ZfEvent,
    run_id: str,
    aliases: dict[str, str],
    events: list[ZfEvent],
) -> bool:
    reason = str(
        (cancellation.payload or {}).get("reason")
        if isinstance(cancellation.payload, dict)
        else ""
    ).strip() or "workflow run cancelled"
    changed = False
    task_ids = {str(cancellation.task_id or "").strip()}
    task_ids.discard("")

    fanout_root = Path(runtime.state_dir) / "fanouts"
    if fanout_root.exists():
        for manifest_path in sorted(fanout_root.glob("*/manifest.json")):
            fanout_id = manifest_path.parent.name
            manifest = runtime._fanout_manifest(fanout_id)
            if not manifest or not _same_run(
                run_id,
                str(
                    manifest.get("workflow_run_id")
                    or manifest.get("trace_id")
                    or ""
                ),
                aliases,
            ):
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                task_id = str(child.get("task_id") or "").strip()
                if task_id:
                    task_ids.add(task_id)
            _release_fanout_children(
                runtime,
                cancellation=cancellation,
                fanout_id=fanout_id,
                manifest=manifest,
                reason=reason,
                events=events,
            )
            if _fanout_terminal(manifest):
                continue
            runtime.event_writer.append(ZfEvent(
                type="fanout.cancelled",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": str(manifest.get("trace_id") or run_id),
                    "workflow_run_id": run_id,
                    "stage_id": str(manifest.get("stage_id") or ""),
                    "trigger_event_id": str(
                        manifest.get("trigger_event_id") or ""
                    ),
                    "target_ref": str(manifest.get("target_ref") or ""),
                    "pdd_id": str(manifest.get("pdd_id") or ""),
                    "feature_id": str(manifest.get("feature_id") or ""),
                    "task_map_ref": str(manifest.get("task_map_ref") or ""),
                    "reason": f"workflow_run_cancelled: {reason}",
                    "source": "run_cancel_reconciliation",
                    "run_cancelled_event_id": cancellation.id,
                },
                causation_id=cancellation.id or None,
                correlation_id=run_id,
            ))
            changed = True

    service = WorkflowOperationService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    for operation in reduce_workflow_operations(events).values():
        if (
            str(operation.get("status") or "") not in _OPERATION_ACTIVE_STATUSES | {"cancelled"}
            or not _same_run(
                run_id,
                str(operation.get("workflow_run_id") or ""),
                aliases,
            )
        ):
            continue
        operation_id = str(operation.get("operation_id") or "")
        request_hash = str(operation.get("request_hash") or "")
        if not operation_id or not request_hash:
            continue
        stop_cancelled_dispatch(
            runtime, role_instance=str(operation.get("role_instance") or ""),
            task_id=str(operation.get("task_id") or ""),
            dispatch_id=str(operation.get("dispatch_id") or ""),
            events=events, cancellation=cancellation,
        )
        service.cancel(
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=run_id,
            reason=f"workflow_run_cancelled: {reason}",
            task_id=str(operation.get("task_id") or ""),
            causation_id=cancellation.id,
            correlation_id=run_id,
        )
        changed = True

    for task in runtime.task_store.list_all():
        if _task_owned_by_run(task, run_id, aliases):
            task_ids.add(task.id)
    for task_id in sorted(task_ids):
        task = runtime.task_store.get(task_id)
        if task is None or task.status in {"done", "cancelled"}:
            continue
        if _task_owned_by_run(task, run_id, aliases):
            stop_cancelled_dispatch(
                runtime, role_instance=str(task.assigned_to or ""),
                task_id=task.id, dispatch_id=str(task.active_dispatch_id or ""),
                events=events, cancellation=cancellation,
            )
        runtime.event_writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "from": task.status,
                "to": "cancelled",
                "source": "run_cancel_reconciliation",
                "trigger_event": cancellation.type,
                "trigger_event_id": cancellation.id,
                "workflow_run_id": run_id,
                "reason": reason,
            },
            causation_id=cancellation.id or None,
            correlation_id=run_id,
        ))
        updated = runtime.task_store.update(
            task_id,
            status="cancelled",
            assigned_to="",
            active_dispatch_id="",
            blocked_reason=f"workflow run {run_id} cancelled: {reason}",
        )
        if updated is not None:
            changed = True
            refresh = getattr(runtime, "_refresh_task_doc_projection", None)
            if callable(refresh):
                try:
                    refresh(
                        updated,
                        source_event="run_cancel_reconciliation",
                    )
                except Exception:
                    pass
    return changed


def _release_fanout_children(
    runtime: Any,
    *,
    cancellation: ZfEvent,
    fanout_id: str,
    manifest: dict,
    reason: str,
    events: list[ZfEvent],
) -> None:
    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        task_id = str(child.get("task_id") or "")
        run_id = str(child.get("run_id") or "")
        role_instance = str(child.get("role_instance") or "")
        child_id = str(child.get("child_id") or "")
        stop_cancelled_dispatch(
            runtime, role_instance=role_instance, task_id=task_id,
            dispatch_id=run_id, events=events, cancellation=cancellation,
        )
        if (
            str(child.get("status") or "") == "dispatched"
            and not _dispatch_lost(
                events,
                fanout_id=fanout_id,
                child_id=child_id,
                run_id=run_id,
            )
        ):
            runtime.event_writer.append(ZfEvent(
                type="fanout.child.dispatch_lost",
                actor="zf-cli",
                task_id=task_id or None,
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": str(manifest.get("trace_id") or ""),
                    "stage_id": str(manifest.get("stage_id") or ""),
                    "child_id": child_id,
                    "run_id": run_id,
                    "role_instance": role_instance,
                    "task_id": task_id,
                    "reason": f"workflow_run_cancelled: {reason}",
                    "source": "run_cancel_reconciliation",
                },
                causation_id=cancellation.id or None,
                correlation_id=str(manifest.get("trace_id") or "") or None,
            ))
        task = runtime.task_store.get(task_id) if task_id else None
        if task is not None and (
            not run_id or str(task.active_dispatch_id or "") == run_id
        ):
            runtime.task_store.update(
                task_id,
                assigned_to="",
                active_dispatch_id="",
            )


def _cancelled_run_id(
    event: ZfEvent,
    aliases: dict[str, str],
) -> str:
    resolved = event_run_id(event, aliases=aliases)
    if resolved:
        return resolved
    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or event.correlation_id
        or ""
    ).strip()


def _same_run(
    expected: str,
    candidate: str,
    aliases: dict[str, str],
) -> bool:
    expected = str(expected or "").strip()
    candidate = str(candidate or "").strip()
    if not expected or not candidate:
        return False
    return aliases.get(expected, expected) == aliases.get(candidate, candidate)


def _task_owned_by_run(
    task: Any,
    run_id: str,
    aliases: dict[str, str],
) -> bool:
    contract = getattr(task, "contract", None)
    evidence = (
        getattr(contract, "evidence_contract", {})
        if contract is not None
        else {}
    )
    if not isinstance(evidence, dict):
        return False
    return any(
        _same_run(run_id, str(evidence.get(key) or ""), aliases)
        for key in ("workflow_run_id", "workflow_request_id", "run_id")
    )


def _fanout_terminal(manifest: dict) -> bool:
    aggregate = (
        manifest.get("aggregate")
        if isinstance(manifest.get("aggregate"), dict)
        else {}
    )
    return (
        str(manifest.get("status") or "") in _FANOUT_TERMINAL_STATUSES
        or str(aggregate.get("status") or "") in _FANOUT_TERMINAL_STATUSES
    )


def _dispatch_lost(
    events: list[ZfEvent],
    *,
    fanout_id: str,
    child_id: str,
    run_id: str,
) -> bool:
    for event in reversed(events):
        if event.type != "fanout.child.dispatch_lost":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("fanout_id") or "") == fanout_id
            and str(payload.get("child_id") or "") == child_id
            and str(payload.get("run_id") or "") == run_id
        ):
            return True
    return False


__all__ = ["reconcile_cancelled_run_resources"]
