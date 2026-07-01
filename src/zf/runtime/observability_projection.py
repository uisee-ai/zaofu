"""Read-only observability projections for Web/API cockpit views."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

EventSlice = Sequence[tuple[int, ZfEvent]]


def observability_span(event: ZfEvent, *, seq: int = 0, project_id: str = "") -> dict[str, Any]:
    """Return a normalized event span with stable trace/run/task identity."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    inferred: list[str] = []
    event_id = str(event.id or f"seq-{seq}")
    task_id = _first_text(event.task_id, payload.get("task_id"), payload.get("parent_task_id"))
    run_id = _first_text(
        payload.get("run_id"),
        payload.get("workflow_run_id"),
        payload.get("fanout_id"),
        payload.get("turn_id"),
    )
    trace_id = _first_text(payload.get("trace_id"), event.correlation_id, run_id, task_id)
    if not trace_id:
        trace_id = f"event:{event_id}"
        inferred.append("trace_id")
    elif not payload.get("trace_id") and not event.correlation_id:
        inferred.append("trace_id")
    span_id = _first_text(payload.get("span_id"), event_id)
    if not payload.get("span_id"):
        inferred.append("span_id")

    return redact_obj({
        "schema_version": "observability-span.v1",
        "project_id": _first_text(project_id, payload.get("project_id"), scope.get("project_id")),
        "trace_id": trace_id,
        "run_id": run_id,
        "task_id": task_id,
        "cycle_id": _first_text(
            payload.get("cycle_id"),
            payload.get("phase_id"),
            payload.get("loop_id"),
            payload.get("iteration_id"),
            run_id,
        ),
        "agent_id": _first_text(
            payload.get("agent_id"),
            payload.get("instance_id"),
            payload.get("target_instance"),
            payload.get("member_id"),
            event.actor,
        ),
        "span_id": span_id,
        "event_id": event_id,
        "seq": seq,
        "ts": event.ts,
        "type": event.type,
        "actor": event.actor or "",
        "status": event_status(event.type, payload),
        "kind": event_kind(event.type),
        "backend": _first_text(
            payload.get("backend"),
            payload.get("provider"),
            payload.get("model"),
            payload.get("transport"),
        ),
        "evidence_refs": _evidence_refs(payload),
        "artifact_refs": _artifact_refs(payload),
        "inferred_fields": inferred,
        "payload": payload,
    })


def build_observability_projection(events: EventSlice, *, project_id: str = "") -> dict[str, Any]:
    """Group event spans into trace summaries without writing runtime state."""

    spans = [observability_span(event, seq=seq, project_id=project_id) for seq, event in events]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        grouped[str(span.get("trace_id") or "")].append(span)
    traces = [_trace_summary(trace_id, trace_spans) for trace_id, trace_spans in grouped.items()]
    traces.sort(key=lambda item: int(item.get("last_seq") or 0), reverse=True)
    return redact_obj({
        "schema_version": "observability-projection.v1",
        "project_id": project_id,
        "trace_count": len(traces),
        "span_count": len(spans),
        "traces": traces,
        "spans": spans,
    })


def build_delivery_closed_loop(trace: dict[str, Any]) -> dict[str, Any]:
    """Project delivery-trace payload into a closed-loop graph contract."""

    trace_id = str(trace.get("trace_id") or "")
    feature_id = str(trace.get("feature_id") or "")
    source_event_ids = [str(item) for item in trace.get("source_event_ids", []) if str(item).strip()][-120:]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    root_id = f"delivery:{trace_id or feature_id or 'synthetic'}"

    _add_node(nodes, seen_nodes, {
        "node_id": root_id,
        "kind": "delivery",
        "title": feature_id or trace_id or "synthetic delivery",
        "status": str(trace.get("status") or ""),
        "project_id": str(trace.get("project_id") or ""),
        "feature_id": feature_id,
        "trace_id": trace_id,
        "evidence_event_ids": source_event_ids[-12:],
        "artifact_refs": _trace_artifacts(trace),
    })
    _add_phase_nodes(trace, nodes, edges, seen_nodes, seen_edges, root_id, trace_id)
    _add_task_nodes(trace, nodes, edges, seen_nodes, seen_edges, root_id, trace_id)
    _add_workflow_spine_nodes(trace, nodes, edges, seen_nodes, seen_edges, root_id, trace_id)
    _add_replan_gate_node(trace, nodes, edges, seen_nodes, seen_edges, root_id, trace_id)
    _add_ship_node(trace, nodes, edges, seen_nodes, seen_edges, root_id, trace_id)

    diagnostics = []
    if not source_event_ids:
        diagnostics.append({
            "kind": "missing_source_events",
            "message": "delivery trace has no source_event_ids; graph still uses task truth",
        })
    return redact_obj({
        "schema_version": "delivery-closed-loop.v1",
        "trace_id": trace_id,
        "project_id": str(trace.get("project_id") or ""),
        "feature_id": feature_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "readiness": trace.get("ship", {}),
        "source_event_ids": source_event_ids,
        "diagnostics": diagnostics,
    })


def event_kind(event_type: str) -> str:
    return event_type.split(".", 1)[0] if "." in event_type else event_type


def event_status(event_type: str, payload: dict[str, Any] | None = None) -> str:
    explicit = str((payload or {}).get("status") or (payload or {}).get("state") or "").strip()
    if explicit:
        return explicit
    lowered = event_type.lower()
    if any(token in lowered for token in ("failed", "blocked", "rejected", "error")):
        return "failed"
    if any(token in lowered for token in ("completed", "done", "passed", "approved", "accepted")):
        return "completed"
    if any(token in lowered for token in ("started", "running", "dispatched", "progress")):
        return "running"
    if any(token in lowered for token in ("queued", "pending", "waiting")):
        return "pending"
    return "observed"


def _trace_summary(trace_id: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_spans = sorted(spans, key=lambda item: int(item.get("seq") or 0))
    first = sorted_spans[0] if sorted_spans else {}
    latest = sorted_spans[-1] if sorted_spans else {}
    return {
        "trace_id": trace_id,
        "first_seq": int(first.get("seq") or 0),
        "last_seq": int(latest.get("seq") or 0),
        "first_ts": str(first.get("ts") or ""),
        "last_ts": str(latest.get("ts") or ""),
        "duration_seconds": _duration_seconds(str(first.get("ts") or ""), str(latest.get("ts") or "")),
        "event_count": len(sorted_spans),
        "task_ids": _sorted_non_empty(span.get("task_id") for span in sorted_spans),
        "actors": _sorted_non_empty(span.get("actor") or span.get("agent_id") for span in sorted_spans),
        "backends": _sorted_non_empty(span.get("backend") for span in sorted_spans),
        "status": str(latest.get("status") or "observed"),
        "last_type": str(latest.get("type") or ""),
        "source_event_ids": [str(span.get("event_id")) for span in sorted_spans if str(span.get("event_id") or "").strip()][-80:],
        "inferred_ids": sorted({
            str(field)
            for span in sorted_spans
            for field in span.get("inferred_fields", [])
            if str(field).strip()
        }),
    }


def _duration_seconds(first_ts: str, last_ts: str) -> int | None:
    try:
        first = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((last - first).total_seconds()))


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _sorted_non_empty(values: Iterable[Any]) -> list[str]:
    return sorted({str(item).strip() for item in values if str(item or "").strip()})


def _evidence_refs(payload: dict[str, Any]) -> list[str]:
    refs = [str(payload[key]) for key in ("evidence_event_id", "event_id", "candidate_ref", "gate_ref") if payload.get(key)]
    for item in payload.get("evidence_events") or payload.get("evidence_event_ids") or []:
        if str(item).strip():
            refs.append(str(item))
    return refs[-20:]


def _artifact_refs(payload: dict[str, Any]) -> list[str]:
    refs = [str(payload[key]) for key in ("artifact_ref", "task_map_ref", "source_index_ref", "plan_ref", "spec_ref") if payload.get(key)]
    nested = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
    for key in ("artifact_ref", "task_map_ref", "source_index_ref"):
        if nested.get(key):
            refs.append(str(nested[key]))
    return refs[-20:]


def _trace_artifacts(trace: dict[str, Any]) -> list[str]:
    artifacts = []
    task_map = trace.get("task_map") if isinstance(trace.get("task_map"), dict) else {}
    if task_map.get("task_map_ref"):
        artifacts.append(str(task_map["task_map_ref"]))
    for item in trace.get("task_map_history") or []:
        if isinstance(item, dict) and item.get("ref"):
            artifacts.append(str(item["ref"]))
    return artifacts[-20:]


def _add_node(nodes: list[dict[str, Any]], seen: set[str], node: dict[str, Any]) -> None:
    node_id = str(node.get("node_id") or "")
    if node_id and node_id not in seen:
        seen.add(node_id)
        nodes.append(node)


def _add_edge(
    edges: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    source: str,
    target: str,
    kind: str,
    *,
    status: str = "",
) -> None:
    if not source or not target:
        return
    key = (source, target, kind)
    if key not in seen:
        seen.add(key)
        edges.append({"from": source, "to": target, "kind": kind, "status": status})


def _add_phase_nodes(
    trace: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seen_nodes: set[str],
    seen_edges: set[tuple[str, str, str]],
    root_id: str,
    trace_id: str,
) -> None:
    for phase in trace.get("phases") or []:
        if not isinstance(phase, dict):
            continue
        phase_id = str(phase.get("phase_id") or phase.get("order") or "")
        if not phase_id:
            continue
        node_id = f"phase:{phase_id}"
        _add_node(nodes, seen_nodes, {
            "node_id": node_id,
            "kind": "phase",
            "title": phase_id,
            "status": str(phase.get("status") or ""),
            "trace_id": trace_id,
            "cycle_id": phase_id,
            "task_ids": [str(item) for item in phase.get("task_ids") or []],
        })
        _add_edge(edges, seen_edges, root_id, node_id, "contains_phase")


def _add_task_nodes(
    trace: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seen_nodes: set[str],
    seen_edges: set[tuple[str, str, str]],
    root_id: str,
    trace_id: str,
) -> None:
    graph = trace.get("execution_graph") if isinstance(trace.get("execution_graph"), dict) else {}
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or "")
        actual = raw.get("actual") if isinstance(raw.get("actual"), dict) else {}
        planned = raw.get("planned") if isinstance(raw.get("planned"), dict) else {}
        if not task_id:
            continue
        node_id = f"task:{task_id}"
        fanouts = [str(item) for item in actual.get("fanout_ids") or [] if str(item).strip()]
        _add_node(nodes, seen_nodes, {
            "node_id": node_id,
            "kind": "task",
            "title": str(raw.get("title") or task_id),
            "status": str(actual.get("status") or ""),
            "task_id": task_id,
            "trace_id": str(actual.get("trace_id") or trace_id),
            "agent_id": str(actual.get("assigned_to") or ""),
            "role_id": str(planned.get("owner_role") or ""),
            "wave": planned.get("wave"),
            "evidence_event_ids": [str(item) for item in actual.get("evidence_events") or [] if str(item).strip()][-20:],
            "artifact_refs": [str(item) for item in actual.get("changed_files") or [] if str(item).strip()][-20:],
            "fanout_ids": fanouts,
            "deep_links": {
                "events": f"page=observability&obs_tab=events&obs_task={task_id}",
                "trace": f"page=observability&obs_tab=traces&obs_trace={trace_id}",
            },
        })
        _add_edge(edges, seen_edges, root_id, node_id, "delivers_task")
        for fanout_id in fanouts:
            fanout_node = f"fanout:{fanout_id}"
            _add_node(nodes, seen_nodes, {
                "node_id": fanout_node,
                "kind": "fanout",
                "title": fanout_id,
                "status": str(actual.get("status") or "running"),
                "trace_id": str(actual.get("trace_id") or trace_id),
                "run_id": fanout_id,
                "task_id": task_id,
            })
            _add_edge(edges, seen_edges, fanout_node, node_id, "executes_task")
    for raw_edge in graph.get("edges") or []:
        if isinstance(raw_edge, dict):
            source = str(raw_edge.get("from") or "")
            target = str(raw_edge.get("to") or "")
            _add_edge(edges, seen_edges, f"task:{source}", f"task:{target}", str(raw_edge.get("kind") or "depends_on"), status=str(raw_edge.get("status") or ""))


def _add_workflow_spine_nodes(
    trace: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seen_nodes: set[str],
    seen_edges: set[tuple[str, str, str]],
    root_id: str,
    trace_id: str,
) -> None:
    spine = trace.get("workflow_spine") if isinstance(trace.get("workflow_spine"), dict) else {}
    for raw in spine.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        fanout_id = str(raw.get("fanout_id") or raw.get("workflow_run_id") or "")
        task_id = str(raw.get("task_id") or "")
        if fanout_id:
            node_id = f"fanout:{fanout_id}"
            _add_node(nodes, seen_nodes, {
                "node_id": node_id,
                "kind": "fanout",
                "title": fanout_id,
                "status": str(raw.get("status") or ""),
                "trace_id": str(raw.get("trace_id") or trace_id),
                "run_id": fanout_id,
                "cycle_id": str(raw.get("stage_id") or ""),
                "topology": str(raw.get("topology") or ""),
                "evidence_event_ids": [str(raw.get("event_id") or "")],
            })
            _add_edge(edges, seen_edges, root_id, node_id, "spawns_fanout")
            if task_id:
                _add_edge(edges, seen_edges, node_id, f"task:{task_id}", "routes_to_task")


def _add_replan_gate_node(
    trace: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seen_nodes: set[str],
    seen_edges: set[tuple[str, str, str]],
    root_id: str,
    trace_id: str,
) -> None:
    gate = trace.get("replan_contract_gate") if isinstance(trace.get("replan_contract_gate"), dict) else {}
    latest = gate.get("latest_eval") if isinstance(gate.get("latest_eval"), dict) else {}
    if not latest and not gate.get("status"):
        return
    node_id = f"gate:replan:{trace_id or 'synthetic'}"
    _add_node(nodes, seen_nodes, {
        "node_id": node_id,
        "kind": "contract_gate",
        "title": "replan contract",
        "status": str(gate.get("status") or latest.get("decision") or ""),
        "trace_id": trace_id,
        "evidence_event_ids": [str(latest.get("event_id") or "")],
        "artifact_refs": [str(item) for item in (latest.get("old_task_map_ref"), latest.get("new_task_map_ref"), latest.get("artifact_ref")) if str(item or "").strip()],
    })
    _add_edge(edges, seen_edges, node_id, root_id, "guards_replan")


def _add_ship_node(
    trace: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seen_nodes: set[str],
    seen_edges: set[tuple[str, str, str]],
    root_id: str,
    trace_id: str,
) -> None:
    ship = trace.get("ship") if isinstance(trace.get("ship"), dict) else {}
    node_id = f"ship:{trace_id or 'synthetic'}"
    _add_node(nodes, seen_nodes, {
        "node_id": node_id,
        "kind": "ship",
        "title": "ship readiness",
        "status": str(ship.get("readiness") or ship.get("status") or ""),
        "trace_id": trace_id,
        "artifact_refs": [str(ship.get("merge_ref") or "")] if ship.get("merge_ref") else [],
        "release_blockers": ship.get("release_blockers") or [],
    })
    _add_edge(edges, seen_edges, root_id, node_id, "ship_readiness")
