"""Replay the missing TaskRef gate after an explicitly reopened blocked Run."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.workflow_operation import reduce_workflow_operations


def replay_blocked_impl_results(
    runtime: Any, *, events: list[ZfEvent],
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> list[ZfEvent]:
    rejected = [e for e in events if e.type == "run.result.rejected"
                and e.payload.get("reason") == "run_terminal:blocked"
                and e.payload.get("terminal_type") == "run.goal.blocked"]
    if not rejected:
        return []
    indexed = {e.id: (i, e) for i, e in enumerate(events)}
    ref_checked = {str(e.payload.get("trigger_event_id") or "") for e in events
                   if e.type in {"task.ref.updated", "task.ref.rejected"}}
    resumes = {str(e.payload.get("workflow_run_id") or e.payload.get("run_id") or ""): e
               for e in events if e.type == "run.goal.updated"
               and e.payload.get("status") in {"active", "running"}}
    runs = build_run_admission_projection(events).runs
    operations = reduce_workflow_operations(events)
    emitted: list[ZfEvent] = []
    for rejection in rejected:
        source_id = str(rejection.payload.get("source_event_id") or "")
        source = indexed.get(source_id, (-1, None))[1]
        if source is None or source.type != "dev.build.done" or source_id in ref_checked:
            continue
        p = source.payload
        task_id = str(source.task_id or "")
        context = generation_contexts.get(task_id)
        run_id = str(p.get("workflow_run_id") or "")
        run = runs.get(run_id)
        resume = resumes.get(run_id)
        op = operations.get(str(p.get("operation_id") or ""), {})
        control = op.get("admitted_control_result_ref") or {}
        if (
            context is None or run is None or run.status != "running"
            or resume is None or indexed[resume.id][0] <= indexed[rejection.id][0]
            or str(rejection.payload.get("workflow_run_id") or rejection.payload.get("run_id") or "") != run_id
            or p.get("task_pipeline_stage") != "impl"
            or op.get("task_pipeline_stage") != "impl"
            or op.get("task_id") != task_id
            or op.get("status") != "settled"
            or op.get("semantic_verdict") != "passed"
            or not op.get("call_result_admitted_event_id")
            or control.get("source_event_id") != source_id
            or not p.get("source_commit")
            or int(p.get("operation_generation") or 0) < 1
            or op.get("operation_generation") != p.get("operation_generation")
            or any(p.get(key) != context.get(key) or op.get(key) != context.get(key)
                   for key in ("workflow_run_id", "task_map_generation"))
        ):
            continue
        task = runtime.task_store.get(task_id)
        if task is None or task.status in {"done", "cancelled"}:
            continue
        if any(
            other.get("task_id") == task_id
            and other.get("workflow_run_id") == run_id
            and other.get("operation_id") != op.get("operation_id")
            and (other.get("status") in {"requested", "running", "suspended"}
                 or int(other.get("operation_generation") or 0) > int(op["operation_generation"]))
            for other in operations.values()
        ):
            continue
        result = runtime._process_task_ref_for_progress_event(source)
        if result is None or result.status not in {"updated", "rejected"}:
            continue
        emitted.append(runtime.event_writer.append(ZfEvent(
            type=f"task.ref.{result.status}", actor="zf-cli", task_id=task_id,
            origin="kernel", causation_id=resume.id, correlation_id=run_id,
            payload={
                **dict(result.payload), "trigger_event_id": source_id,
                "source": "blocked_impl_result_recovery",
                "run_result_rejection_event_id": rejection.id,
                "resume_event_id": resume.id,
            },
        )))
        ref_checked.add(source_id)
    return emitted
