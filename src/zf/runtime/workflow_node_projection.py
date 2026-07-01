"""Read-only WorkflowNode / StageRun projection."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.workflow.graph import WorkflowGraph, WorkflowNode
from zf.runtime.workflow_conditions import (
    WorkflowConditionEvaluator,
    WorkflowEvaluationContext,
)


PHASES = {
    "pending", "ready", "running", "succeeded", "skipped",
    "failed", "error", "omitted", "blocked",
}


def build_workflow_node_projection(
    *,
    graph: WorkflowGraph,
    events: list[ZfEvent],
    tasks: list[Task] | dict[str, Task] | None = None,
) -> dict[str, Any]:
    task_map = _tasks_by_id(tasks)
    evaluator = WorkflowConditionEvaluator()
    runs: list[dict[str, Any]] = []
    if task_map:
        for task in task_map.values():
            task_events = _task_events(events, task.id)
            for node in graph.nodes:
                runs.append(_node_run(
                    node=node,
                    graph=graph,
                    task=task,
                    events=task_events,
                    evaluator=evaluator,
                ))
    else:
        for node in graph.nodes:
            runs.append(_node_run(
                node=node,
                graph=graph,
                task=None,
                events=events,
                evaluator=evaluator,
            ))
    return redact_obj({
        "schema_version": "workflow-node-run.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph": graph.to_dict(),
        "runs": runs,
        "counts": {
            "nodes": len(graph.nodes),
            "runs": len(runs),
            "blocked": len([run for run in runs if run["phase"] == "blocked"]),
            "ready": len([run for run in runs if run["phase"] == "ready"]),
        },
    })


def _node_run(
    *,
    node: WorkflowNode,
    graph: WorkflowGraph,
    task: Task | None,
    events: list[ZfEvent],
    evaluator: WorkflowConditionEvaluator,
) -> dict[str, Any]:
    trigger_event = _latest_trigger_event(node, events)
    evaluation = evaluator.evaluate_node(
        node,
        WorkflowEvaluationContext(
            events=events,
            task=task,
            trigger_event=trigger_event,
        ),
    )
    blocking = [
        condition.reason for condition in evaluation.conditions
        if not condition.passed
    ]
    phase = _phase(
        node,
        task,
        events,
        evaluation.ready,
        blocking_reasons=blocking,
        trigger_event=trigger_event,
    )
    started, finished = _times(node, events)
    return {
        "task_id": task.id if task is not None else "",
        "stage_id": node.stage_id,
        "node_id": node.node_id,
        "type": node.type,
        "phase": phase,
        "boundary_id": _boundary_id(node),
        "children": _children(node, graph),
        "outbound_nodes": _outbound_nodes(node, graph),
        "message": "; ".join(blocking[:3]),
        "started_at": started,
        "finished_at": finished,
        "blocking_reasons": blocking,
        "source_event_ids": _source_event_ids(node, events),
        "action_decisions": _action_decisions(node, events),
        "evaluation": evaluation.to_dict(),
    }


def _tasks_by_id(tasks: list[Task] | dict[str, Task] | None) -> dict[str, Task]:
    if tasks is None:
        return {}
    if isinstance(tasks, dict):
        return tasks
    return {task.id: task for task in tasks}


def _task_events(events: list[ZfEvent], task_id: str) -> list[ZfEvent]:
    return [
        event for event in events
        if not event.task_id or event.task_id == task_id
    ]


def _latest_trigger_event(node: WorkflowNode, events: list[ZfEvent]) -> ZfEvent | None:
    trigger_types = {item.strip() for item in node.trigger.split(",") if item.strip()}
    if not trigger_types:
        return None
    return next((event for event in reversed(events) if event.type in trigger_types), None)


def _phase(
    node: WorkflowNode,
    task: Task | None,
    events: list[ZfEvent],
    ready: bool,
    *,
    blocking_reasons: list[str],
    trigger_event: ZfEvent | None,
) -> str:
    if _events_of_type(events, node.failure_event):
        return "failed"
    if _events_of_type(events, node.skipped_event):
        return "skipped"
    if _events_of_type(events, node.success_event):
        return "succeeded"
    if task is not None and task.status == "blocked":
        return "blocked"
    if trigger_event is not None and blocking_reasons:
        return "blocked"
    if task is not None and task.status in {"in_progress", "review", "test", "testing", "judge", "dispatched"}:
        return "running" if not ready else "ready"
    return "ready" if ready else "pending"


def _events_of_type(events: list[ZfEvent], event_type: str) -> list[ZfEvent]:
    if not event_type:
        return []
    candidates = {item.strip() for item in event_type.split(",") if item.strip()}
    return [event for event in events if event.type in candidates]


def _source_event_ids(node: WorkflowNode, events: list[ZfEvent]) -> list[str]:
    candidates = {
        item for item in (
            node.trigger, node.success_event, node.failure_event, node.skipped_event
        )
        if item
    }
    split_candidates: set[str] = set()
    for candidate in candidates:
        split_candidates.update(item.strip() for item in candidate.split(",") if item.strip())
    return [event.id for event in events if event.type in split_candidates]


def _action_decisions(node: WorkflowNode, events: list[ZfEvent]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        stage_id = str(payload.get("stage_id") or "")
        if stage_id and stage_id != node.stage_id:
            continue
        action_type = str(payload.get("action_type") or "")
        if not action_type and event.type not in {
            "workflow.dispatch.requested",
            "task.rework.requested",
            "workflow.gate.requested",
            "static_gate.passed",
            "static_gate.failed",
            "static_gate.skipped",
        }:
            continue
        out.append({
            "event_id": event.id,
            "event_type": event.type,
            "action_type": action_type or _action_type_from_event(event.type),
            "decision": str(payload.get("decision") or payload.get("status") or ""),
            "reason": str(payload.get("reason") or payload.get("skip_reason") or ""),
        })
    return out


def _action_type_from_event(event_type: str) -> str:
    if event_type == "workflow.dispatch.requested":
        return "dispatch_role"
    if event_type == "task.rework.requested":
        return "route_rework"
    if event_type in {"workflow.gate.requested", "static_gate.passed", "static_gate.failed", "static_gate.skipped"}:
        return "run_gate"
    return ""


def _times(node: WorkflowNode, events: list[ZfEvent]) -> tuple[str, str]:
    # Compute the source-id set ONCE, not once per event: the previous
    # `if event.id in set(_source_event_ids(node, events))` re-scanned every
    # event for every event -> O(events^2) per node (404k calls / 74s on the
    # cangjie-r2 graph). Hoisting makes node timing O(events).
    source_ids = set(_source_event_ids(node, events))
    source_events = [event for event in events if event.id in source_ids]
    if not source_events:
        return "", ""
    started = source_events[0].ts
    finished = source_events[-1].ts if node.success_event or node.failure_event else ""
    return started, finished


def _children(node: WorkflowNode, graph: WorkflowGraph) -> list[str]:
    return [
        edge.to_node for edge in graph.edges
        if edge.from_node == node.node_id
    ]


def _outbound_nodes(node: WorkflowNode, graph: WorkflowGraph) -> list[str]:
    children = set(_children(node, graph))
    outbound = [
        edge.to_node for edge in graph.edges
        if edge.from_node in children
    ]
    return outbound or list(children)


def _boundary_id(node: WorkflowNode) -> str:
    if ":" in node.stage_id:
        return node.stage_id.split(":", 1)[0]
    return node.stage_id


def node_run_to_dict(run: object) -> dict[str, Any]:
    if hasattr(run, "__dataclass_fields__"):
        return asdict(run)
    return dict(run) if isinstance(run, dict) else {}
