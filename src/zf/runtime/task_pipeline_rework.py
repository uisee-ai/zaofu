"""TaskRef admission failures that require a new v4 Impl operation."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent


_TASK_REF_STATUS_EVENTS = frozenset({"task.ref.rejected", "task.ref.updated"})


def derive_impl_rework_requests(
    *,
    events: Iterable[ZfEvent],
    generation_contexts: Mapping[str, Mapping[str, Any]],
    operation_rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Project current typed Impl TaskRef rejections into semantic rework.

    A TaskRef gate runs after the provider call result is admitted.  If it
    rejects that exact typed result, the settled Impl operation cannot be
    reopened.  The current rejection therefore requests the next v4 operation
    generation; it must never fall through to the legacy role-v1 repair path.
    """

    event_rows = list(events)
    event_by_id = {event.id: event for event in event_rows}
    operations = {
        str(row.get("operation_id") or ""): dict(row)
        for row in operation_rows
        if str(row.get("operation_id") or "")
    }
    latest_status_by_trigger: dict[str, str] = {}
    for event in event_rows:
        if event.type not in _TASK_REF_STATUS_EVENTS:
            continue
        payload = _payload(event)
        trigger_id = str(payload.get("trigger_event_id") or "").strip()
        if trigger_id:
            latest_status_by_trigger[trigger_id] = event.type

    from zf.runtime.task_pipeline_operator_retry import operator_impl_retry_requests
    requests = operator_impl_retry_requests(
        events=event_rows, operations=operations, contexts=generation_contexts,
    )
    for event in event_rows:
        if event.type != "task.ref.rejected" or not event.task_id:
            continue
        task_id = str(event.task_id)
        context = generation_contexts.get(task_id)
        if context is None:
            continue
        rejection = _payload(event)
        if str(rejection.get("rejection_kind") or "") == "stale_contract_result":
            continue
        source_event_id = str(
            rejection.get("trigger_event_id") or ""
        ).strip()
        if (
            not source_event_id
            or latest_status_by_trigger.get(source_event_id)
            != "task.ref.rejected"
        ):
            continue
        source_event = event_by_id.get(source_event_id)
        if source_event is None or source_event.type != "dev.build.done":
            continue
        source = _payload(source_event)
        operation_id = str(source.get("operation_id") or "").strip()
        operation = operations.get(operation_id)
        generation = int(source.get("operation_generation") or 0)
        if (
            str(source.get("task_pipeline_stage") or "") != "impl"
            or generation < 1
            or operation is None
            or str(operation.get("status") or "") != "settled"
            or str(operation.get("semantic_verdict") or "").lower()
            not in {"", "passed", "pass", "approved", "approve", "admit"}
            or str(operation.get("task_pipeline_stage") or "") != "impl"
            or int(operation.get("operation_generation") or 0) != generation
            or str(operation.get("workflow_run_id") or "")
            != str(context.get("workflow_run_id") or "")
            or str(operation.get("task_map_generation") or "")
            != str(context.get("task_map_generation") or "")
            or not str(operation.get("call_result_admitted_event_id") or "")
        ):
            continue
        requests[task_id] = {
            "schema_version": "task-pipeline-impl-rework-request.v1",
            "fault": "task_ref_admission_rejected",
            "event_id": event.id,
            "source_event_id": source_event_id,
            "operation_id": operation_id,
            "operation_generation": generation,
            "reason": str(
                rejection.get("reason") or "TaskRef admission rejected"
            ).strip(),
            "expected_action": _expected_action(rejection),
            "changed_files": _strings(rejection.get("changed_files")),
            "out_of_scope_files": _strings(
                rejection.get("out_of_scope_files")
            ),
            "dirty_files": _strings(rejection.get("dirty_files")),
        }
    return requests


def reconcile_task_ref_repair_replays(
    runtime: Any,
    *,
    events: Iterable[ZfEvent],
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> list[ZfEvent]:
    """Retry the mechanical TaskRef gate before spending another Impl turn.

    The provider result and its operation are already terminal when TaskRef
    admission runs. A later kernel/config repair can make that same immutable
    result admissible, so replay only the ref gate once per repair request and
    let normal semantic rework handle results that remain invalid.
    """

    event_rows = list(events)
    event_by_id = {event.id: event for event in event_rows}
    latest_status_by_trigger: dict[str, str] = {}
    latest_repair_by_source: dict[str, ZfEvent] = {}
    for event in event_rows:
        payload = _payload(event)
        if event.type in _TASK_REF_STATUS_EVENTS:
            trigger_id = str(payload.get("trigger_event_id") or "").strip()
            if trigger_id:
                latest_status_by_trigger[trigger_id] = event.type
        elif event.type == "task.ref.repair.requested":
            source_id = str(payload.get("source_event_id") or "").strip()
            if source_id:
                latest_repair_by_source[source_id] = event

    attempted = getattr(runtime, "_task_ref_repair_replay_attempts", None)
    if attempted is None:
        attempted = set()
        runtime._task_ref_repair_replay_attempts = attempted
    emitted: list[ZfEvent] = []
    for source_id, repair in latest_repair_by_source.items():
        if repair.id in attempted:
            continue
        attempted.add(repair.id)
        if latest_status_by_trigger.get(source_id) != "task.ref.rejected":
            continue
        source = event_by_id.get(source_id)
        if source is None or source.type != "dev.build.done" or not source.task_id:
            continue
        context = generation_contexts.get(str(source.task_id))
        source_payload = _payload(source)
        if (
            context is None
            or str(source_payload.get("task_pipeline_stage") or "") != "impl"
            or str(source_payload.get("workflow_run_id") or "")
            != str(context.get("workflow_run_id") or "")
            or str(source_payload.get("task_map_generation") or "")
            != str(context.get("task_map_generation") or "")
        ):
            continue
        processor = getattr(runtime, "_process_task_ref_for_progress_event", None)
        if not callable(processor):
            continue
        try:
            result = processor(source)
        except Exception:
            continue
        if result is None or str(getattr(result, "status", "")) != "updated":
            continue
        payload = dict(getattr(result, "payload", {}) or {})
        payload.update({
            "source": "task_ref_repair_reconcile",
            "repair_request_event_id": repair.id,
        })
        emitted.append(runtime.event_writer.append(ZfEvent(
            type="task.ref.updated",
            actor="zf-cli",
            task_id=source.task_id,
            payload=payload,
            causation_id=repair.id,
            correlation_id=source.correlation_id or repair.correlation_id,
            origin="kernel",
        )))
    return emitted


def impl_rework_feedback(
    request: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Render one projected TaskRef rejection for the next Impl briefing."""

    if not request:
        return []
    return [{
        "finding_id": f"task-ref:{request.get('event_id') or 'rejected'}",
        "category": str(request.get("fault") or "task_ref_admission"),
        "severity": "blocking",
        "reason": str(request.get("reason") or "TaskRef admission rejected"),
        "expected_action": str(
            request.get("expected_action")
            or "repair_task_ref_and_resubmit_typed_impl_result"
        ),
        "source_event_id": str(request.get("source_event_id") or ""),
        "blocking_event_id": str(request.get("event_id") or ""),
        "changed_files": _strings(request.get("changed_files")),
        "out_of_scope_files": _strings(request.get("out_of_scope_files")),
        "dirty_files": _strings(request.get("dirty_files")),
    }]


def _expected_action(payload: Mapping[str, Any]) -> str:
    if _strings(payload.get("out_of_scope_files")):
        return "repair_source_scope_and_resubmit_typed_impl_result"
    if _strings(payload.get("dirty_files")):
        return "settle_workspace_and_resubmit_typed_impl_result"
    return "repair_task_ref_and_resubmit_typed_impl_result"


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


__all__ = [
    "derive_impl_rework_requests",
    "impl_rework_feedback",
    "reconcile_task_ref_repair_replays",
]
