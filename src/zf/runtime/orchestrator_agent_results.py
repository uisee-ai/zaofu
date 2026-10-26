"""Select current execution evidence for OA without introducing a result store."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import reduce_workflow_operations


PIPELINE_RESULT_EVENTS = frozenset(
    {
        "dev.build.done",
        "dev.blocked",
        "task.pipeline.verify.completed",
        "task.pipeline.verify.failed",
        "task.pipeline.acceptance.completed",
        "task.pipeline.acceptance.failed",
    }
)


def current_result_events(
    events: Sequence[ZfEvent],
    *,
    source_event: ZfEvent,
    workflow_run_id: str,
    task_map_generation: str,
    result_event_types: frozenset[str],
) -> list[ZfEvent]:
    """Keep canonical latest stage results, including retained partial-plan tasks.

    Pipeline currentness comes from admitted per-task generations and operations;
    unscoped legacy events cannot enter another Run's evidence pack.
    """
    contexts = task_pipeline_generation_contexts(events)
    operations = reduce_workflow_operations(events)
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    for operation in operations.values():
        task_id = str(operation.get("task_id") or "")
        context = contexts.get(task_id)
        if context is None or any(
            str(operation.get(key) or "") != str(context.get(key) or "")
            for key in ("workflow_run_id", "task_map_generation")
        ):
            continue
        stage = str(operation.get("task_pipeline_stage") or "")
        key = (task_id, stage)
        if stage and int(operation.get("operation_generation") or 0) >= int(
            latest.get(key, {}).get("operation_generation") or 0
        ):
            latest[key] = operation
    selected = []
    rows = list(events)
    if all(event.id != source_event.id for event in rows):
        rows.append(source_event)
    for event in rows:
        if (
            event.id != source_event.id
            and event.type not in result_event_types | PIPELINE_RESULT_EVENTS
        ):
            continue
        p = event.payload if isinstance(event.payload, Mapping) else {}
        run_id = str(
            p.get("workflow_run_id")
            or p.get("run_id")
            or p.get("trace_id")
            or event.correlation_id
            or ""
        )
        if run_id != workflow_run_id:
            continue
        if str(p.get("control_result_schema") or "") in {
            "orchestration-decision.v1",
            "owner-delivery-narrative.v1",
        }:
            continue
        stage = str(p.get("task_pipeline_stage") or "")
        if stage:
            operation = latest.get(
                (str(event.task_id or p.get("task_id") or ""), stage), {}
            )
            if (
                operation.get("status") != "settled"
                or not p.get("operation_id")
                or p.get("operation_id") != operation.get("operation_id")
                or p.get("task_map_generation") != operation.get("task_map_generation")
                or int(p.get("operation_generation") or 0)
                != int(operation.get("operation_generation") or 0)
                or p.get("control_result_ref")
                != operation.get("admitted_control_result_ref")
            ):
                continue
        else:
            if event.type in PIPELINE_RESULT_EVENTS:
                continue
            generation = str(p.get("task_map_generation") or "")
            if task_map_generation and generation and generation != task_map_generation:
                continue
        selected.append(event)
    return selected
