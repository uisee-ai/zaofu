"""LangSmith-style span and autoresearch graph projections for Delivery."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.delivery_projection_common import (
    EventSlice,
    event_error,
    event_status,
    evidence_refs,
    payload,
    tools_count,
)

_AUTORESEARCH_TYPES = {
    "autoresearch.trigger.accepted",
    "autoresearch.trigger.skipped",
    "autoresearch.invocation.requested",
    "autoresearch.loop.requested",
    "autoresearch.loop.started",
    "autoresearch.loop.completed",
    "autoresearch.loop.failed",
    "autoresearch.baseline.scored",
    "autoresearch.candidate.scored",
    "autoresearch.ab.completed",
    "autoresearch.reflection.completed",
    "autoresearch.replan.proposed",
    "autoresearch.deposition.recorded",
    "autoresearch.bug_candidate.created",
    "autoresearch.repair.prepared",
    "autoresearch.repair.dispatch_requested",
    "autoresearch.repair.dispatched",
    "autoresearch.validation.passed",
    "autoresearch.validation.failed",
}


def build_run_trace(
    *,
    events: EventSlice,
    tasks: dict[str, Task],
    run_groups: list[dict[str, Any]],
    autoresearch_cycles: list[dict[str, Any]],
) -> dict[str, Any]:
    spans = _run_group_spans(run_groups) + _event_spans(events, run_groups)
    timeline = _timeline(events)
    autoresearch_graphs = _autoresearch_graphs(events, autoresearch_cycles)
    return {
        "schema_version": "delivery-run-trace.v1",
        "trace_id": _trace_id(events, tasks),
        "span_count": len(spans),
        "timeline_count": len(timeline),
        "spans": spans,
        "timeline": timeline,
        "usage_summary": _usage_summary(events, spans),
        "autoresearch_graphs": autoresearch_graphs,
        "diagnostics": [] if spans else [{
            "kind": "delivery_run_trace_empty",
            "message": "no run groups or events available for span projection",
        }],
    }


def _run_group_spans(run_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for group in run_groups:
        span_id = f"run:{group['group_id']}"
        spans.append({
            "trace_id": str(group["group_id"]),
            "span_id": span_id,
            "parent_span_id": "",
            "task_id": "",
            "run_id": str(group["group_id"]),
            "fanout_id": str(group["group_id"]) if str(group["kind"]) == "fanout" else "",
            "role": str(group.get("kind") or ""),
            "instance_id": "",
            "backend": "",
            "status": group["status"],
            "started_at": group.get("started_at") or "",
            "ended_at": group.get("ended_at") or "",
            "duration_ms": group.get("duration_ms"),
            "tokens_input": 0,
            "tokens_output": 0,
            "tools_count": 0,
            "error": group.get("verdict") if group["status"] == "failed" else {},
            "evidence_refs": group.get("artifact_refs") or [],
            "raw_event_refs": group.get("source_event_ids") or [],
        })
        spans.extend(_child_spans(group, span_id))
    return spans


def _child_spans(group: dict[str, Any], parent_span_id: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for child in group.get("children") or []:
        if not isinstance(child, dict):
            continue
        child_id = str(child.get("child_id") or child.get("run_id") or "")
        spans.append({
            "trace_id": str(group["group_id"]),
            "span_id": f"child:{group['group_id']}:{child_id}",
            "parent_span_id": parent_span_id,
            "task_id": str(child.get("task_id") or ""),
            "run_id": child_id,
            "fanout_id": str(group["group_id"]),
            "role": str(child.get("role") or ""),
            "instance_id": str(child.get("worker_id") or child.get("role_instance") or ""),
            "backend": str(child.get("backend") or ""),
            "status": str(child.get("status") or "pending"),
            "started_at": str(child.get("started_at") or ""),
            "ended_at": str(child.get("ended_at") or ""),
            "duration_ms": child.get("duration_ms"),
            "tokens_input": int(child.get("tokens_input") or child.get("input_tokens") or 0),
            "tokens_output": int(child.get("tokens_output") or child.get("output_tokens") or 0),
            "tools_count": int(child.get("tools_count") or 0),
            "error": child.get("error") or {},
            "evidence_refs": child.get("evidence_refs") or [],
            "raw_event_refs": child.get("source_event_ids") or [],
        })
    return spans


def _event_spans(events: EventSlice, run_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_by_event: dict[str, str] = {}
    for group in run_groups:
        for event_id in group.get("source_event_ids") or []:
            group_by_event[str(event_id)] = f"run:{group['group_id']}"
    spans: list[dict[str, Any]] = []
    for seq, event in events[-160:]:
        data = payload(event)
        event_id = event.id or f"seq-{seq}"
        spans.append({
            "trace_id": str(event.correlation_id or data.get("trace_id") or data.get("fanout_id") or "delivery"),
            "span_id": f"event:{seq}",
            "parent_span_id": group_by_event.get(event_id, ""),
            "task_id": str(event.task_id or data.get("task_id") or ""),
            "run_id": str(data.get("run_id") or data.get("dispatch_id") or ""),
            "fanout_id": str(data.get("fanout_id") or ""),
            "role": str(data.get("role") or data.get("role_instance") or event.actor or ""),
            "instance_id": str(data.get("instance_id") or data.get("worker_id") or ""),
            "backend": str(data.get("backend") or ""),
            "status": event_status(event),
            "started_at": event.ts,
            "ended_at": event.ts if event_status(event) in {"passed", "failed", "done"} else "",
            "duration_ms": data.get("duration_ms"),
            "tokens_input": int(data.get("tokens_input") or data.get("input_tokens") or 0),
            "tokens_output": int(data.get("tokens_output") or data.get("output_tokens") or 0),
            "tools_count": tools_count(data),
            "error": event_error(event),
            "evidence_refs": evidence_refs(data),
            "raw_event_refs": [event_id],
        })
    return spans


def _autoresearch_graphs(
    events: EventSlice,
    cycles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[int, ZfEvent]]] = {}
    for item in cycles:
        cycle_id = str(item.get("cycle_id") or "")
        if cycle_id:
            groups.setdefault(cycle_id, [])
    for seq, event in events:
        if not (event.type.startswith("autoresearch.") or event.type in _AUTORESEARCH_TYPES):
            continue
        data = payload(event)
        key = str(
            event.correlation_id
            or data.get("loop_request_id")
            or data.get("trigger_id")
            or data.get("run_id")
            or event.task_id
            or "autoresearch"
        )
        groups.setdefault(key, []).append((seq, event))
    return [
        graph for key, grouped in groups.items()
        if (graph := _autoresearch_graph(key, grouped))
    ]


def _autoresearch_graph(key: str, grouped: list[tuple[int, ZfEvent]]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    prev = ""
    mode = "single_candidate"
    for seq, event in grouped:
        data = payload(event)
        node_id = f"ar:{seq}"
        node = {
            "node_id": node_id,
            "kind": _autoresearch_node_kind(event.type),
            "event_type": event.type,
            "event_id": event.id,
            "task_id": str(event.task_id or data.get("task_id") or ""),
            "status": event_status(event),
            "label": _autoresearch_label(event.type, data),
            "baseline_score": data.get("baseline_score"),
            "candidate_score": data.get("candidate_score"),
            "score_delta": data.get("score_delta"),
            "winner": str(data.get("winner") or ""),
            "deposition": str(data.get("deposition") or ""),
            "ts": event.ts,
        }
        if node["baseline_score"] is not None and node["candidate_score"] is not None:
            mode = "ab"
        nodes.append(node)
        if prev:
            edges.append({"from": prev, "to": node_id, "kind": "event_order"})
        prev = node_id
    if not nodes:
        return {}
    return {
        "schema_version": "autoresearch-execution-graph.v1",
        "graph_id": key,
        "comparison_mode": mode,
        "status": nodes[-1]["status"],
        "nodes": nodes,
        "edges": edges,
        "source_event_ids": [node["event_id"] for node in nodes if node["event_id"]],
    }


def _timeline(events: EventSlice) -> list[dict[str, Any]]:
    return [
        {
            "seq": seq,
            "event_id": event.id,
            "event_type": event.type,
            "task_id": str(event.task_id or payload(event).get("task_id") or ""),
            "status": event_status(event),
            "ts": event.ts,
        }
        for seq, event in events[-240:]
    ]


def _usage_summary(events: EventSlice, spans: list[dict[str, Any]]) -> dict[str, Any]:
    by_backend: dict[str, dict[str, int]] = {}
    for span in spans:
        backend = str(span.get("backend") or "unknown")
        bucket = by_backend.setdefault(backend, {"input_tokens": 0, "output_tokens": 0, "tools_count": 0})
        bucket["input_tokens"] += int(span.get("tokens_input") or 0)
        bucket["output_tokens"] += int(span.get("tokens_output") or 0)
        bucket["tools_count"] += int(span.get("tools_count") or 0)
    return {
        "input_tokens": sum(item["input_tokens"] for item in by_backend.values()),
        "output_tokens": sum(item["output_tokens"] for item in by_backend.values()),
        "tools_count": sum(item["tools_count"] for item in by_backend.values()),
        "event_count": len(events),
        "by_backend": by_backend,
    }


def _trace_id(events: EventSlice, tasks: dict[str, Task]) -> str:
    for _seq, event in events:
        data = payload(event)
        for key in ("trace_id", "correlation_id", "fanout_id"):
            value = str(data.get(key) or "")
            if value:
                return value
        if event.correlation_id:
            return event.correlation_id
    feature_ids = [task.contract.feature_id for task in tasks.values() if task.contract.feature_id]
    return f"delivery:{feature_ids[0]}" if feature_ids else "delivery:synthetic"


def _autoresearch_node_kind(event_type: str) -> str:
    if "trigger" in event_type or event_type.endswith(".requested"):
        return "trigger"
    if "baseline" in event_type:
        return "baseline"
    if "candidate" in event_type:
        return "candidate"
    if ".ab." in event_type:
        return "ab_eval"
    if "reflection" in event_type:
        return "reflection"
    if "replan" in event_type:
        return "replan"
    if "repair" in event_type:
        return "repair"
    if "validation" in event_type:
        return "validation"
    return "loop"


def _autoresearch_label(event_type: str, data: dict[str, Any]) -> str:
    return str(
        data.get("label")
        or data.get("reason")
        or data.get("trigger")
        or event_type.replace("autoresearch.", "")
    )
