"""Task-flow projection for the Delivery cockpit."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.delivery_projection_common import (
    EventSlice,
    base_stage_id,
    dedupe,
    human_label,
    payload,
    normalize_status,
    status_kind,
)


def build_task_flow(
    *,
    config: Any,
    events: EventSlice,
    tasks: dict[str, Task],
    workflow_trace: dict[str, Any],
    execution_graph: dict[str, Any],
) -> dict[str, Any]:
    """Build ``delivery-task-flow.v1`` from YAML stages, roles, tasks and events."""

    order = _stage_order(config, workflow_trace=workflow_trace, tasks=tasks)
    labels = _stage_labels(config, workflow_trace)
    role_stage = _role_stage_index(config)
    workflow_task_stage = _workflow_task_stage_index(workflow_trace)
    graph_nodes = {
        str(node.get("task_id") or ""): node
        for node in execution_graph.get("nodes") or []
        if isinstance(node, dict) and str(node.get("task_id") or "")
    }
    task_events = _events_by_task(events)
    buckets = _task_buckets(
        tasks,
        order=order,
        role_stage=role_stage,
        workflow_task_stage=workflow_task_stage,
    )

    stages = [
        _task_flow_stage(
            stage_id=stage_id,
            label=labels.get(stage_id) or human_label(stage_id),
            tasks=buckets.get(stage_id, []),
            events=events,
            task_events=task_events,
            graph_nodes=graph_nodes,
            workflow_trace=workflow_trace,
        )
        for stage_id in order
    ]
    return {
        "schema_version": "delivery-task-flow.v1",
        "stage_order": order,
        "active_stage_ids": [
            stage["stage_id"] for stage in stages
            if stage["status"] in {"running", "ready", "blocked", "failed"}
        ],
        "stages": stages,
        "metrics": {
            "stage_total": len(stages),
            "task_total": len(tasks),
            "task_done": sum(1 for task in tasks.values() if status_kind(task.status) == "done"),
            "task_running": sum(1 for task in tasks.values() if status_kind(task.status) == "running"),
            "task_failed": sum(1 for task in tasks.values() if status_kind(task.status) == "failed"),
            "task_blocked": sum(1 for task in tasks.values() if status_kind(task.status) == "blocked"),
        },
        "diagnostics": _task_flow_diagnostics(config, order, workflow_trace),
    }


def _stage_order(config: Any, *, workflow_trace: dict[str, Any], tasks: dict[str, Task]) -> list[str]:
    workflow = getattr(config, "workflow", None)
    dag = getattr(workflow, "dag", None) if workflow is not None else None
    candidates = [
        str(item) for item in list(getattr(dag, "stage_order", []) or [])
        if str(item).strip()
    ]
    if not candidates and workflow is not None:
        candidates.extend(
            str(getattr(stage, "id", "") or "")
            for stage in list(getattr(workflow, "stages", []) or [])
        )
    if not candidates:
        for run in workflow_trace.get("stage_runs") or []:
            if not isinstance(run, dict):
                continue
            stage_id = base_stage_id(str(run.get("stage_id") or ""))
            if stage_id and not stage_id.startswith(("role:", "rework:", "terminal:")):
                candidates.append(stage_id)
    if not candidates:
        candidates.extend(
            str(task.contract.phase or "")
            for task in tasks.values()
            if str(task.contract.phase or "").strip()
        )
    return dedupe([item for item in candidates if item.strip()]) or ["tasks"]


def _stage_labels(config: Any, workflow_trace: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    raw = getattr(config, "stage_labels", {}) if config is not None else {}
    if isinstance(raw, dict):
        labels.update({str(k): str(v) for k, v in raw.items()})
    for run in workflow_trace.get("stage_runs") or []:
        if not isinstance(run, dict):
            continue
        stage_id = base_stage_id(str(run.get("stage_id") or ""))
        label = str(run.get("label") or "")
        if stage_id and label and not stage_id.startswith(("role:", "terminal:", "rework:")):
            labels.setdefault(stage_id, label.replace(" aggregate", ""))
    return labels


def _role_stage_index(config: Any) -> dict[str, str]:
    index: dict[str, str] = {}
    roles = list(getattr(config, "roles", []) or []) if config is not None else []
    for role in roles:
        stages = [str(item) for item in list(getattr(role, "stages", []) or []) if str(item)]
        if not stages:
            continue
        stage = stages[0]
        for key in (getattr(role, "name", ""), getattr(role, "instance_id", ""), getattr(role, "role_id", "")):
            if str(key or "").strip():
                index[str(key)] = stage
    return index


def _workflow_task_stage_index(workflow_trace: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for run in workflow_trace.get("stage_runs") or []:
        if not isinstance(run, dict):
            continue
        stage_id = base_stage_id(str(run.get("stage_id") or ""))
        if not stage_id:
            continue
        for task_id in run.get("task_ids") or []:
            if str(task_id):
                index.setdefault(str(task_id), stage_id)
    return index


def _task_buckets(
    tasks: dict[str, Task],
    *,
    order: list[str],
    role_stage: dict[str, str],
    workflow_task_stage: dict[str, str],
) -> dict[str, list[Task]]:
    buckets: dict[str, list[Task]] = {stage_id: [] for stage_id in order}
    unmatched: list[Task] = []
    for task in tasks.values():
        stage_id = _task_stage_id(
            task,
            order=order,
            role_stage=role_stage,
            workflow_task_stage=workflow_task_stage,
        )
        if stage_id and stage_id in buckets:
            buckets[stage_id].append(task)
        else:
            unmatched.append(task)
    if unmatched:
        fallback = _fallback_task_stage(order)
        buckets.setdefault(fallback, [])
        buckets[fallback].extend(unmatched)
        if fallback not in order:
            order.append(fallback)
    return buckets


def _task_stage_id(
    task: Task,
    *,
    order: list[str],
    role_stage: dict[str, str],
    workflow_task_stage: dict[str, str],
) -> str:
    phase = str(task.contract.phase or "")
    if phase in order:
        return phase
    for key in (task.contract.owner_instance, task.contract.owner_role, task.assigned_to or ""):
        stage = role_stage.get(str(key or ""))
        if stage in order:
            return stage
    stage = workflow_task_stage.get(task.id, "")
    return stage if stage in order else ""


def _task_flow_stage(
    *,
    stage_id: str,
    label: str,
    tasks: list[Task],
    events: EventSlice,
    task_events: dict[str, list[tuple[int, ZfEvent]]],
    graph_nodes: dict[str, dict[str, Any]],
    workflow_trace: dict[str, Any],
) -> dict[str, Any]:
    statuses = [status_kind(task.status) for task in tasks]
    source_ids = _stage_event_ids(events, stage_id)
    return {
        "stage_id": stage_id,
        "label": label,
        "status": _aggregate_status(statuses, bool(source_ids)),
        "tasks_total": len(tasks),
        "tasks_done": statuses.count("done"),
        "tasks_running": statuses.count("running"),
        "tasks_failed": statuses.count("failed"),
        "tasks_blocked": statuses.count("blocked"),
        "active_task_ids": [
            task.id for task in tasks
            if status_kind(task.status) in {"running", "blocked", "failed"}
        ],
        "task_ids": [task.id for task in sorted(tasks, key=lambda item: item.id)],
        "tasks": [
            _task_summary(task, task_events.get(task.id, []), graph_nodes.get(task.id, {}))
            for task in sorted(tasks, key=lambda item: item.id)
        ],
        "run_group_ids": _run_group_ids(workflow_trace, stage_id),
        "gate_summary": _stage_gate_summary(workflow_trace, stage_id),
        "source_event_ids": source_ids[-40:],
        "diagnostics": [],
    }


def _task_summary(
    task: Task,
    task_events: list[tuple[int, ZfEvent]],
    graph_node: dict[str, Any],
) -> dict[str, Any]:
    latest = task_events[-1][1] if task_events else None
    actual = graph_node.get("actual") if isinstance(graph_node.get("actual"), dict) else {}
    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "assigned_to": str(task.assigned_to or ""),
        "phase": str(task.contract.phase or ""),
        "owner_role": str(task.contract.owner_role or ""),
        "owner_instance": str(task.contract.owner_instance or ""),
        "blocked_by": list(task.blocked_by or []),
        "evidence_event_ids": list(actual.get("evidence_events") or []),
        "latest_event": {
            "event_id": latest.id,
            "event_type": latest.type,
            "ts": latest.ts,
        } if latest else {},
        "source_event_ids": [event.id for _seq, event in task_events if event.id][-20:],
    }


def _run_group_ids(workflow_trace: dict[str, Any], stage_id: str) -> list[str]:
    return dedupe(
        str(run.get("fanout_id") or f"stage:{run.get('stage_id')}")
        for run in workflow_trace.get("stage_runs") or []
        if isinstance(run, dict) and base_stage_id(str(run.get("stage_id") or "")) == stage_id
    )


def _stage_gate_summary(workflow_trace: dict[str, Any], stage_id: str) -> dict[str, Any]:
    matches = [
        run for run in workflow_trace.get("stage_runs") or []
        if isinstance(run, dict) and base_stage_id(str(run.get("stage_id") or "")) == stage_id
    ]
    latest = matches[-1] if matches else {}
    return {
        "status": normalize_status(str(latest.get("status") or "pending")) if latest else "pending",
        "verdict": latest.get("verdict") or {},
        "source_event_ids": latest.get("source_event_ids") or [],
    }


def _events_by_task(events: EventSlice) -> dict[str, list[tuple[int, ZfEvent]]]:
    index: dict[str, list[tuple[int, ZfEvent]]] = {}
    for seq, event in events:
        task_id = str(event.task_id or payload(event).get("task_id") or "")
        if task_id:
            index.setdefault(task_id, []).append((seq, event))
    return index


def _stage_event_ids(events: EventSlice, stage_id: str) -> list[str]:
    ids: list[str] = []
    for _seq, event in events:
        data = payload(event)
        if base_stage_id(str(data.get("stage_id") or data.get("phase") or "")) == stage_id:
            ids.append(event.id)
    return ids


def _task_flow_diagnostics(
    config: Any,
    order: list[str],
    workflow_trace: dict[str, Any],
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    workflow = getattr(config, "workflow", None)
    dag = getattr(workflow, "dag", None) if workflow is not None else None
    if not list(getattr(dag, "stage_order", []) or []):
        diagnostics.append({
            "kind": "stage_order_fallback",
            "message": "workflow.dag.stage_order is empty; task flow was derived from stages/events/tasks",
        })
    if not workflow_trace.get("stage_runs"):
        diagnostics.append({
            "kind": "workflow_trace_empty",
            "message": "no workflow stage runs; task flow is kanban/task-only",
        })
    if order == ["tasks"]:
        diagnostics.append({
            "kind": "task_flow_generic",
            "message": "no stage hints found; using generic task bucket",
        })
    return diagnostics


def _aggregate_status(statuses: list[str], has_events: bool) -> str:
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "running" for status in statuses):
        return "running"
    if any(status == "blocked" for status in statuses):
        return "blocked"
    if statuses and all(status == "done" for status in statuses):
        return "done"
    if has_events:
        return "running"
    return "pending"


def _fallback_task_stage(order: list[str]) -> str:
    for stage in order:
        if stage not in {"done", "release", "ship", "terminal:done"}:
            return stage
    return order[0] if order else "tasks"
