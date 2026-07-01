"""Workflow stage trace projection for Delivery Trace.

``workflow-trace.v1`` is a read-only projection: it combines the static
workflow graph compiled from ``zf.yaml`` with runtime events and task state.
It never writes runtime state and never calls providers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.workflow.graph import WorkflowGraph, WorkflowNode, compile_workflow_graph
from zf.runtime.workflow_trace_fanout import (
    build_fanout_runs,
    fanout_metrics,
    fanout_stage_status,
    fanouts_by_stage,
    strip_internal_child_events,
)

EventSlice = Sequence[tuple[int, ZfEvent]]
EventInput = Sequence[tuple[int, ZfEvent] | ZfEvent]

_ACTIVE_STATUSES = {"ready", "running", "aggregating", "rerunning"}
_BLOCKING_UPSTREAM = {"failed", "blocked"}
_PASSING_UPSTREAM = {"passed", "skipped"}


def build_workflow_trace(
    *,
    config: Any,
    events: EventInput,
    tasks: list[Task] | dict[str, Task] | None = None,
    feature_id: str = "",
    task_map_ref: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """Build ``workflow-trace.v1`` from config, events, and tasks."""

    diagnostics: list[dict[str, str]] = []
    normalized_events = _normalize_events(events)
    task_map = _tasks_by_id(tasks)
    visible_events = _visible_events(
        normalized_events,
        task_ids=set(task_map),
        feature_id=feature_id,
    )
    graph = _compile_graph(config, diagnostics)
    fanout_runs = build_fanout_runs(visible_events)
    fanout_index = fanouts_by_stage(fanout_runs)

    primitive_runs = [
        _stage_run(
            node=node,
            graph=graph,
            events=visible_events,
            task_map=task_map,
            fanouts=fanout_index.get(_base_stage_id(node.stage_id), []),
        )
        for node in graph.nodes
    ]
    runs_by_node = {run["node_id"]: run for run in primitive_runs}
    stage_runs = [
        _apply_upstream_status(run, graph=graph, runs_by_node=runs_by_node)
        for run in primitive_runs
    ]
    active_stage_ids = [
        run["stage_id"] for run in stage_runs
        if run["status"] in _ACTIVE_STATUSES
    ]
    result = {
        "schema_version": "workflow-trace.v1",
        "workflow_id": "default",
        "project_id": project_id,
        "feature_id": feature_id,
        "task_map_ref": task_map_ref,
        "config_ref": "zf.yaml" if config is not None else "",
        "graph": graph.to_dict(),
        "stage_runs": stage_runs,
        "fanout_runs": fanout_runs,
        "active_stage_ids": active_stage_ids,
        "metrics": _trace_metrics(stage_runs, fanout_runs),
        "diagnostics": diagnostics + list(graph.diagnostics),
        "source_event_ids": [event.id for _seq, event in visible_events if event.id],
    }
    return redact_obj(result)


def _compile_graph(config: Any, diagnostics: list[dict[str, str]]) -> WorkflowGraph:
    if config is None:
        diagnostics.append({
            "kind": "workflow_config_missing",
            "message": "workflow trace built without config; stage skeleton is empty",
        })
        return WorkflowGraphCompilerFallback().empty()
    try:
        return compile_workflow_graph(config)
    except Exception as exc:  # pragma: no cover - defensive projection boundary
        diagnostics.append({
            "kind": "workflow_graph_compile_failed",
            "message": str(exc),
        })
        return WorkflowGraphCompilerFallback().empty()


class WorkflowGraphCompilerFallback:
    """Tiny fallback to keep projection callers from crashing without config."""

    def empty(self) -> WorkflowGraph:
        from zf.core.workflow.graph import (
            DerivedWorkflowEventSets,
            TerminalPolicy,
            WorkflowGraph,
        )

        return WorkflowGraph(
            nodes=(),
            edges=(),
            event_sets=DerivedWorkflowEventSets(
                handoff_success_events=frozenset(),
                stage_progress_events=frozenset(),
                rework_trigger_events=frozenset(),
                rework_triage_trigger_events=frozenset(),
                terminal_success_events=frozenset(),
                readonly_gate_success_events=frozenset(),
            ),
            terminal_policy=TerminalPolicy(),
        )


def _stage_run(
    *,
    node: WorkflowNode,
    graph: WorkflowGraph,
    events: EventSlice,
    task_map: dict[str, Task],
    fanouts: list[dict[str, Any]],
) -> dict[str, Any]:
    node_events = _node_events(node, events)
    event_types = [event.type for _seq, event in node_events]
    success_events = _events_of_type(node_events, node.success_event)
    failure_events = _events_of_type(node_events, node.failure_event)
    skipped_events = _events_of_type(node_events, node.skipped_event)
    trigger_events = _events_of_type(node_events, node.trigger)
    fanout_status = fanout_stage_status(fanouts)
    status = _node_status(
        node=node,
        event_types=event_types,
        success_events=success_events,
        failure_events=failure_events,
        skipped_events=skipped_events,
        trigger_events=trigger_events,
        fanout_status=fanout_status,
    )
    source_event_ids = [event.id for _seq, event in node_events if event.id]
    started_at, ended_at = _time_bounds(node_events, terminal=status in {"passed", "failed", "skipped"})
    child_runs = [
        child
        for fanout in fanouts
        for child in fanout.get("child_runs", [])
    ]
    task_ids = sorted({
        str(event.task_id or _payload(event).get("task_id") or "")
        for _seq, event in node_events
        if str(event.task_id or _payload(event).get("task_id") or "")
    } | {
        str(child.get("task_id") or "")
        for child in child_runs
        if str(child.get("task_id") or "")
    })
    artifact_refs = _artifact_refs(node_events)
    latest_failure = failure_events[-1][1] if failure_events else None
    failure_reason = ""
    if latest_failure is None:
        failed_children = [child for child in child_runs if child.get("status") == "failed"]
        if failed_children:
            latest_child = failed_children[-1]
            error = latest_child.get("error") if isinstance(latest_child.get("error"), dict) else {}
            failure_reason = str(error.get("message") or latest_child.get("failure_reason") or "")
    return {
        "stage_id": node.stage_id,
        "node_id": node.node_id,
        "label": node.label or node.stage_id,
        "kind": _stage_kind(node),
        "operator_kind": _operator_kind(node),
        "status": status,
        "attempt": _attempt(node_events),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": _duration_ms(started_at, ended_at),
        "queue_wait_ms": _queue_wait_ms(node_events),
        "upstream_stage_ids": _neighbor_stage_ids(graph, node.node_id, direction="upstream"),
        "downstream_stage_ids": _neighbor_stage_ids(graph, node.node_id, direction="downstream"),
        "trigger_events": _event_names(node.trigger),
        "output_events": _event_names(node.success_event) + _event_names(node.failure_event),
        "source_event_ids": source_event_ids,
        "fanout_id": fanouts[-1]["fanout_id"] if fanouts else "",
        "fanout_child_runs": strip_internal_child_events(child_runs),
        "task_ids": task_ids or sorted(_task_ids_for_node(node, task_map)),
        "artifact_refs": artifact_refs,
        "metrics": _stage_metrics(fanouts),
        "verdict": _verdict(status, latest_failure, fallback_reason=failure_reason),
        "metadata": node.metadata,
    }


def _node_status(
    *,
    node: WorkflowNode,
    event_types: list[str],
    success_events: EventSlice,
    failure_events: EventSlice,
    skipped_events: EventSlice,
    trigger_events: EventSlice,
    fanout_status: str,
) -> str:
    if failure_events:
        return "failed"
    if skipped_events:
        return "skipped"
    if success_events:
        return "passed"
    if fanout_status in {"passed", "failed", "aggregating", "running"}:
        return fanout_status
    if node.type == "aggregate_stage" and any(t.startswith("fanout.child.") for t in event_types):
        return "aggregating"
    if trigger_events or event_types:
        return "running"
    return "pending"


def _apply_upstream_status(
    run: dict[str, Any],
    *,
    graph: WorkflowGraph,
    runs_by_node: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if run["status"] != "pending":
        return run
    upstream = [
        runs_by_node.get(edge.from_node)
        for edge in graph.edges
        if edge.to_node == run["node_id"]
    ]
    upstream = [item for item in upstream if item is not None]
    if any(item["status"] in _BLOCKING_UPSTREAM for item in upstream):
        return {**run, "status": "blocked"}
    if upstream and all(item["status"] in _PASSING_UPSTREAM for item in upstream):
        return {**run, "status": "ready"}
    return run


def _stage_metrics(fanouts: list[dict[str, Any]]) -> dict[str, Any]:
    children = [
        child
        for fanout in fanouts
        for child in fanout.get("child_runs", [])
    ]
    counts = fanout_metrics(children)
    aggregate_waits = [
        int(wait)
        for fanout in fanouts
        if (wait := fanout.get("metrics", {}).get("aggregate_wait_ms")) is not None
    ]
    return {
        **counts,
        "tokens_input": 0,
        "tokens_output": 0,
        "cost_usd": None,
        "aggregate_wait_ms": max(aggregate_waits) if aggregate_waits else None,
    }


def _trace_metrics(stage_runs: list[dict[str, Any]], fanout_runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stage_total": len(stage_runs),
        "stage_passed": len([run for run in stage_runs if run["status"] == "passed"]),
        "stage_running": len([run for run in stage_runs if run["status"] in {"running", "aggregating"}]),
        "stage_failed": len([run for run in stage_runs if run["status"] == "failed"]),
        "stage_blocked": len([run for run in stage_runs if run["status"] == "blocked"]),
        "fanout_total": len(fanout_runs),
        "children_total": sum(int(run.get("metrics", {}).get("children_total") or 0) for run in fanout_runs),
        "critical_path_ms": sum(
            int(run.get("duration_ms") or 0)
            for run in stage_runs
            if run.get("status") not in {"pending", "blocked", "skipped"}
        ) or None,
    }


def _normalize_events(events: EventInput) -> list[tuple[int, ZfEvent]]:
    normalized: list[tuple[int, ZfEvent]] = []
    for index, item in enumerate(events):
        if isinstance(item, tuple):
            normalized.append((int(item[0]), item[1]))
        else:
            normalized.append((index, item))
    return normalized


def _visible_events(events: EventSlice, *, task_ids: set[str], feature_id: str) -> list[tuple[int, ZfEvent]]:
    if not feature_id and not task_ids:
        return list(events)
    out: list[tuple[int, ZfEvent]] = []
    for seq, event in events:
        payload = _payload(event)
        event_feature = str(payload.get("feature_id") or payload.get("pdd_id") or "")
        event_task = str(event.task_id or payload.get("task_id") or "")
        if event_feature == feature_id or (event_task and event_task in task_ids):
            out.append((seq, event))
    return out


def _node_events(node: WorkflowNode, events: EventSlice) -> list[tuple[int, ZfEvent]]:
    wanted = set(_event_names(node.trigger))
    wanted.update(_event_names(node.success_event))
    wanted.update(_event_names(node.failure_event))
    wanted.update(_event_names(node.skipped_event))
    base_stage = _base_stage_id(node.stage_id)
    out: list[tuple[int, ZfEvent]] = []
    for seq, event in events:
        payload = _payload(event)
        event_stage = _base_stage_id(str(payload.get("stage_id") or ""))
        linked = event.type in wanted if wanted else False
        linked = linked or (event_stage and event_stage == base_stage)
        if node.type == "aggregate_stage" and event.type.startswith("fanout.child.") and event_stage == base_stage:
            linked = True
        if linked:
            out.append((seq, event))
    return out


def _events_of_type(events: EventSlice, event_spec: str) -> list[tuple[int, ZfEvent]]:
    names = set(_event_names(event_spec))
    if not names:
        return []
    return [(seq, event) for seq, event in events if event.type in names]


def _event_names(event_spec: str) -> list[str]:
    return [item.strip() for item in str(event_spec or "").split(",") if item.strip()]


def _neighbor_stage_ids(graph: WorkflowGraph, node_id: str, *, direction: str) -> list[str]:
    node_by_id = {node.node_id: node for node in graph.nodes}
    ids: list[str] = []
    for edge in graph.edges:
        candidate = edge.from_node if direction == "upstream" and edge.to_node == node_id else ""
        if direction == "downstream" and edge.from_node == node_id:
            candidate = edge.to_node
        node = node_by_id.get(candidate)
        if node is not None:
            ids.append(node.stage_id)
    return sorted(dict.fromkeys(ids))


def _stage_kind(node: WorkflowNode) -> str:
    if node.type == "fanout_stage":
        return "fanout"
    if node.type == "aggregate_stage":
        return "aggregate"
    if node.type == "gate_stage":
        return "gate"
    if node.type == "terminal_gate":
        return "terminal"
    if node.type == "rework_route":
        return "rework"
    return "role"


def _operator_kind(node: WorkflowNode) -> str:
    text = f"{node.stage_id} {node.type} {node.action}".lower()
    if "fanout" in text or "aggregate" in text or "synth" in text:
        return "synthesize"
    if "gate" in text or "verify" in text or "review" in text or "judge" in text or "test" in text:
        return "verify"
    if "route" in text or "classify" in text:
        return "classify"
    if "rework" in text or "retry" in text or "loop" in text:
        return "loop"
    if "filter" in text:
        return "filter"
    if "compare" in text or "score" in text:
        return "compare"
    return "dispatch"


def _base_stage_id(stage_id: str) -> str:
    return stage_id.split(":", 1)[0]


def _task_ids_for_node(node: WorkflowNode, task_map: dict[str, Task]) -> set[str]:
    if not node.roles:
        return set()
    roles = set(node.roles)
    return {
        task.id for task in task_map.values()
        if str(task.contract.owner_role or "") in roles
        or str(task.assigned_to or "") in roles
        or str(task.contract.owner_instance or "") in roles
    }


def _attempt(events: EventSlice) -> int:
    attempts = [
        int(_payload(event).get("attempt") or 0)
        for _seq, event in events
        if str(_payload(event).get("attempt") or "").isdigit()
    ]
    return max(attempts) if attempts else 1


def _time_bounds(events: EventSlice, *, terminal: bool) -> tuple[str, str]:
    if not events:
        return "", ""
    started = events[0][1].ts
    ended = events[-1][1].ts if terminal else ""
    return started, ended


def _duration_ms(started_at: str, ended_at: str) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((ended - started).total_seconds() * 1000))


def _queue_wait_ms(events: EventSlice) -> int | None:
    ready_at = ""
    for _seq, event in events:
        if _is_ready_event(event):
            ready_at = event.ts
            break
    if not ready_at:
        return None
    ready_seen = False
    for _seq, event in events:
        if not ready_seen:
            ready_seen = event.ts == ready_at and _is_ready_event(event)
        if not ready_seen:
            continue
        if _is_start_event(event):
            return _duration_ms(ready_at, event.ts)
    return None


def _is_ready_event(event: ZfEvent) -> bool:
    if event.type.endswith((".ready", ".requested", ".accepted")):
        return True
    status = str(_payload(event).get("status") or "").lower()
    return status in {"ready", "queued", "requested", "accepted"}


def _is_start_event(event: ZfEvent) -> bool:
    if event.type.endswith((".started", ".dispatched", ".running")):
        return True
    status = str(_payload(event).get("status") or "").lower()
    return status in {"running", "started", "dispatched"}


def _artifact_refs(events: EventSlice) -> list[str]:
    refs: list[str] = []
    for _seq, event in events:
        payload = _payload(event)
        for key in ("artifact_ref", "task_map_ref", "plan_ref", "report_ref", "source_index_ref"):
            value = str(payload.get(key) or "")
            if value:
                refs.append(value)
        values = payload.get("artifact_refs")
        if isinstance(values, list):
            refs.extend(str(value) for value in values if str(value))
    return sorted(dict.fromkeys(refs))


def _verdict(status: str, failure_event: ZfEvent | None, *, fallback_reason: str = "") -> dict[str, str]:
    payload = _payload(failure_event) if failure_event is not None else {}
    return {
        "status": status if status in {"passed", "failed", "blocked", "skipped"} else "",
        "reason": str(payload.get("reason") or payload.get("message") or fallback_reason),
        "evidence_event_id": failure_event.id if failure_event is not None else "",
    }


def _tasks_by_id(tasks: list[Task] | dict[str, Task] | None) -> dict[str, Task]:
    if tasks is None:
        return {}
    if isinstance(tasks, dict):
        return tasks
    return {task.id: task for task in tasks}


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None:
        return {}
    return event.payload if isinstance(event.payload, dict) else {}
