"""Human handoff facts over the existing Task Pipeline projection and stores."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_run_binding import canonical_task_run


def pipeline_handoff(*, state_dir: Path, project_root: Path, config: Any,
                     task: Any, events: list) -> dict[str, Any] | None:
    if task is None:
        return None
    context = task_pipeline_generation_contexts(events).get(task.id)
    dispatched = [event for event in events if event.task_id == task.id
                  and event.type == "task.pipeline.stage.dispatched"]
    if not context and not dispatched:
        return None
    result: dict[str, Any] = {
        "route": "task_pipeline", "status": "unavailable", "current_stage": "unknown",
        "owner": {"role": "", "instance_id": ""}, "next_required_event": "",
        "next_required_action": "核对当前任务的流程与执行依据",
        "source_event_ids": [], "workspace": {"status": "unavailable"},
        "admitted_results": [],
        "authorizes_execution": False,
    }
    run_id, strict = canonical_task_run(task, events)
    if not context or (strict and (not run_id or context.get("workflow_run_id") != run_id)):
        result["reason"] = "current_generation_unavailable"
        return result
    from zf.runtime.task_pipeline_projection import read_task_pipeline_projection
    try:
        projection = read_task_pipeline_projection(state_dir, project_root=project_root, config=config)
        view = next(row for row in projection["tasks"] if row["task_id"] == task.id)
        stage = str(view.get("pipeline_stage") or "unknown")
        operation = next((row for row in projection["operations"]
                          if row["operation_id"] == view.get("current_operation_id")), {})
        live = bool(operation) and task.status not in {"done", "cancelled"}
        current_stage = (view.get("current_stage") if live else "") or stage
        dispatch = next((event for event in reversed(dispatched)
                         if event.payload.get("operation_id") == operation.get("operation_id")
                         and event.payload.get("dispatch_id") == task.active_dispatch_id), None) if live else None
        facts = dispatch.payload if dispatch else {}
        admitted = [{"kind": "admitted_result", "event_id": row["last_event_id"],
                     "summary": f"{row['stage']} · generation {row['operation_generation']} · "
                                f"{row.get('semantic_verdict') or 'verdict unknown'}"}
                    for row in projection["operations"]
                    if row["task_id"] == task.id and row["status"] == "settled"]
        result.update({
            "status": "current", "current_stage": current_stage, "pipeline_stage": stage,
            "workflow_run_id": view["workflow_run_id"], "generation_id": view["generation_id"],
            "operation_id": view["current_operation_id"] if live else "",
            "attempt_id": facts.get("attempt_id", ""), "dispatch_id": facts.get("dispatch_id", ""),
            "target_ref": facts.get("task_ref") or facts.get("target_ref") or "",
            "owner": {"role": view.get("current_stage", "") if live else "",
                      "instance_id": view.get("current_worker", "") if live else ""},
            "blockers": list(view.get("blockers") or []),
            "admitted_results": admitted,
            "next_required_event": (str(facts.get("canonical_success_event") or "workflow.operation.settled")
                                    if live else "task.pipeline.stage.dispatched" if stage.endswith("_ready") else ""),
            "next_required_action": ("等待当前阶段提交并接纳结果" if live else
                                     "等待调度下一阶段" if stage.endswith("_ready") else
                                     "" if stage in {"done", "cancelled"} else "等待当前流程条件满足"),
            "source_event_ids": [value for value in [context.get("generation_admitted_event_id"),
                                   operation.get("last_event_id"), dispatch.id if dispatch else ""] if value],
        })
        if live:
            from zf.runtime.task_workspace_binding import resolve_task_workspace
            try:
                root = resolve_task_workspace(state_dir=state_dir, project_root=project_root,
                    config=config, task=task, events=events, require_live=True)
                result["workspace"] = {"status": "bound", "path": str(root)} if root else {"status": "unavailable"}
            except (OSError, ValueError) as exc:
                result["workspace"] = {"status": "unavailable", "reason": str(exc)}
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result["reason"] = str(exc) or "pipeline_projection_unavailable"
    return result
