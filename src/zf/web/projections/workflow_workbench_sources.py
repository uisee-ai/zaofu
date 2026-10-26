"""Read exact Request/Run inputs for the SDLC workbench; never discover by time."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.goal_claim_set import hydrate_pinned_goal_claim_set
from zf.runtime.plan_preview_artifacts import read_preview_artifact
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts


def scope_events(events, *, request_id: str, run_id: str, tasks) -> list[tuple[int, ZfEvent]]:
    """Explicit ownership beats causal reachability, including foreign Task runs."""
    task_runs = {t.id: t.execution_binding.workflow_run_id for t in tasks}
    generations = task_pipeline_generation_contexts(events)
    owned_tasks = {key for key, value in task_runs.items() if run_id and value == run_id}
    owned_tasks.update(key for key, value in generations.items()
                       if run_id and value.get("workflow_run_id") == run_id)
    matched: dict[str, tuple[int, ZfEvent]] = {}
    owned_operations, owned_fanouts = set(), set()
    candidate_parents = set()
    for _ in range(len(events) + 1):
        before = len(matched)
        for seq, event in enumerate(events, 1):
            p = event.payload
            candidate = event.type.startswith("candidate.")
            requested = p.get("requested_tasks") if candidate else None
            receipts = [str(item.get("approval_event_id") or "") if isinstance(item, dict) else ""
                        for item in requested] if isinstance(requested, list) else []
            candidate_receipts_owned = bool(receipts) and all(ref and ref in matched for ref in receipts)
            explicit_run = str(p.get("workflow_run_id") or "")
            explicit_request = str(p.get("request_id") or "")
            task_id = str(event.task_id or p.get("task_id") or "")
            if explicit_run and explicit_run != run_id:
                continue
            if explicit_request and explicit_request != request_id:
                continue
            if receipts and not candidate_receipts_owned:
                continue
            # A Task may be rebound later; its current Store binding cannot
            # erase an occurrence explicitly owned by this historical Run.
            if task_runs.get(task_id) and task_runs[task_id] != run_id and not (
                explicit_run and explicit_run == run_id or run_id and event.correlation_id == run_id
            ):
                continue
            if (explicit_run and explicit_run == run_id
                    or explicit_request == request_id
                    or run_id and event.correlation_id == run_id
                    or task_id in owned_tasks
                    or p.get("operation_id") in owned_operations
                    or p.get("fanout_id") in owned_fanouts
                    or candidate_receipts_owned
                    or candidate and event.id in candidate_parents
                    or event.causation_id in matched):
                matched[event.id] = (seq, event)
                if candidate and event.causation_id:
                    candidate_parents.add(event.causation_id)
                if p.get("operation_id"):
                    owned_operations.add(p["operation_id"])
                if p.get("fanout_id"):
                    owned_fanouts.add(p["fanout_id"])
        if len(matched) == before:
            break
    return sorted(matched.values(), key=lambda pair: pair[0])


def read_workbench_sources(context, detail: dict, *, as_of: int = 0) -> dict[str, Any]:
    request = detail["result"]
    request_id = str(request["request_id"])
    run_id = str(request.get("run_id") or detail.get("links", {}).get("run_id") or "")
    all_events = event_log_from_project(context.state_dir, config=context.config).read_all()
    source_seq = min(as_of, len(all_events)) if as_of else len(all_events)
    events = all_events[:source_seq]
    all_tasks = TaskStore(context.state_dir / "kanban.json").list_all_with_archive()
    scoped = scope_events(events, request_id=request_id, run_id=run_id, tasks=all_tasks)
    task_ids = {str(e.task_id or e.payload.get("task_id") or "") for _, e in scoped}
    generations = task_pipeline_generation_contexts(events)
    task_ids.update(key for key, value in generations.items()
                    if run_id and value.get("workflow_run_id") == run_id)
    tasks = {t.id: t for t in all_tasks if t.id in task_ids and (
        not t.execution_binding.workflow_run_id or t.execution_binding.workflow_run_id == run_id)}
    diagnostics: list[str] = []
    plans, versions = [], []
    seen_plans, seen_maps = set(), set()
    for seq, event in scoped:
        p = event.payload
        if event.type in {"plan.approval.requested", "replan.requested", "task_map.ready"} or (
            event.type == "approval.requested" and p.get("plan_preview")
        ):
            plan_id = str(p.get("plan_id") or event.id)
            if plan_id not in seen_plans:
                preview = build_plan_preview(context.state_dir, [e for _, e in scoped],
                                             project_root=context.project_root, plan_id=plan_id)
                if preview.get("ok"):
                    plans.append(preview)
                    seen_plans.add(plan_id)
        ref = str(p.get("task_map_ref") or "")
        prior = next((v for v in reversed(versions) if v["ref"] == ref), {})
        digest = str(p.get("task_map_digest") or prior.get("digest") or "")
        admitted = event.type in {"task.pipeline.generation.admitted", "task_map.materialization.committed", "task_map.admitted"}
        if event.type not in {"task_map.ready", "plan.approval.requested", "approval.requested",
                              "task.pipeline.generation.admitted", "task_map.materialization.committed", "task_map.admitted", "replan.requested"}:
            continue
        identity = (ref, digest, admitted)
        if not ref or identity in seen_maps:
            continue
        seen_maps.add(identity)
        try:
            value = json.loads(read_preview_artifact(ref, state_dir=context.state_dir,
                project_root=context.project_root, digest=digest))
            if not isinstance(value, dict):
                raise ValueError("task map must be an object")
            versions.append({"ref": ref, "digest": digest, "event_id": event.id,
                             "goal_id": p.get("goal_id") or prior.get("goal_id", ""),
                             "task_map_generation": p.get("task_map_generation") or prior.get("task_map_generation", ""),
                             "task_map_digest": digest,
                             "seq": seq, "body": value,
                             "admitted": admitted})
        except (OSError, ValueError) as exc:
            diagnostics.append(f"task map unavailable: {ref}: {exc}")
    snapshot = detail.get("artifacts", {}).get("effective_config", {})
    compiled = snapshot.get("compiled_graph", {})
    if not compiled:
        diagnostics.append("此配置快照尚未包含编译图；重新准备 Proposal 后可显示结构。")
    if as_of:
        diagnostics.append("历史回放使用事件游标；Task 当前态不作为历史状态。需求与配置取本 Proposal 快照，不重建旧准备修订。")
    goal_claim_set = None
    if versions:
        version = versions[-1]
        pins = [e for _, e in scoped if e.type == "goal.claim_set.pinned" and e.origin == "kernel"]
        current_pin = next((e.payload for e in reversed(pins)
                            if run_id and e.payload.get("workflow_run_id") == run_id
                            and version.get("task_map_generation")
                            and same_task_map_generation(str(e.payload.get("task_map_generation") or ""),
                                                         version["task_map_generation"])), {})
        try:
            goal_claim_set = hydrate_pinned_goal_claim_set(state_dir=context.state_dir,
                events=pins, workflow_run_id=run_id,
                goal_id=str(current_pin.get("goal_id") or version.get("goal_id") or version["body"].get("goal_id") or ""),
                task_map_generation=str(version.get("task_map_generation") or ""))
        except (ValueError, OSError) as exc:
            diagnostics.append(f"Goal Claim Set 不可用：{exc}")
    return {"scope": {"project_id": request.get("project_id", ""), "request_id": request_id,
                       "revision": request.get("revision"), "run_id": run_id,
                       "proposal_digest": request.get("proposal_digest", ""),
                       "source_seq": source_seq, "head_seq": len(all_events), "historical": bool(as_of),
                       "execution_started": any(e.type == "workflow.submit.accepted" for _, e in scoped)
                       if as_of else bool(detail.get("lifecycle", {}).get("submitted"))},
            "request": request, "requirement": detail.get("requirement", {}),
            "compiled": compiled, "events": scoped, "tasks": tasks, "goal_claim_set": goal_claim_set,
            "plans": plans, "versions": versions, "diagnostics": diagnostics}
