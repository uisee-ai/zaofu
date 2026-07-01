"""Thick Delivery Trace projection (doc94).

Read-only projection over an already-resolved delivery trace plus EventLog
events. It aggregates owner-produced signals; it does not re-judge runtime
truth or mutate state.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.delivery_projection_common import event_status, payload

EventSlice = list[tuple[int, ZfEvent]]

_P0_BEHAVIOR_EVENTS = {"task.rework.triage.completed", "worker.stuck", "worker.probe.silent"}
_P0_EVAL_PREFIXES = ("discriminator.", "gate.", "static_gate.", "meta_gate.")
_YELLOW_BEHAVIOR_EVENTS = {"behavior.source_coverage_gap.detected"}
_YELLOW_EVAL_EVENTS = {
    "eval.contract_completeness.completed",
    "eval.evidence_sufficiency.completed",
    "replan.contract_eval.completed",
}


def build_delivery_thick_trace(
    *,
    trace: dict[str, Any],
    events: EventSlice,
    generated_at: str,
    project_id: str = "",
) -> dict[str, Any]:
    """Build ``delivery-thick-trace.v1`` from already-loaded projections."""

    spans = [_normalize_span(span) for span in ((trace.get("trace") or {}).get("spans") or [])]
    scoped_events, excluded_event_count = _scope_events(trace, events)
    behaviors = _behavior_overlay(scoped_events)
    evals = _eval_overlay(scoped_events)
    graph = _graph(trace=trace, events=scoped_events, behaviors=behaviors, evals=evals)
    result = {
        "schema_version": "delivery-thick-trace.v1",
        "generated_at": generated_at,
        "project_id": project_id or str(trace.get("project_id") or ""),
        "target": {
            "id": str(trace.get("feature_id") or ""),
            "trace_id": str(trace.get("trace_id") or ""),
            "status": str(trace.get("status") or ""),
            "workflow_archetype": str(trace.get("workflow_archetype") or ""),
            "synthetic": bool(trace.get("synthetic")),
        },
        "graph": graph,
        "spans": spans,
        "span_count": len(spans),
        "behaviors": behaviors,
        "evals": evals,
        "artifacts": _artifacts(trace),
        "improvement_candidates": _improvement_candidates(behaviors, evals),
        "cursor": trace.get("cursor") or {},
        "diagnostics": _diagnostics(trace, spans, event_count=len(events), excluded_event_count=excluded_event_count),
        "otel": {
            "compatible": True,
            "collector_required": False,
            "export_format": "otlp-json",
        },
    }
    return redact_obj(result)


def _scope_events(trace: dict[str, Any], events: EventSlice) -> tuple[EventSlice, int]:
    scope = _event_scope(trace)
    if not any(scope.values()):
        return events, 0

    relevant_ids: set[str] = set()
    indexed: dict[str, tuple[int, ZfEvent]] = {}
    children_by_parent: dict[str, list[tuple[int, ZfEvent]]] = defaultdict(list)
    for seq, event in events:
        if event.id:
            indexed[event.id] = (seq, event)
        parent = str(event.causation_id or "")
        if parent:
            children_by_parent[parent].append((seq, event))
        if _event_matches_scope(event, scope):
            if event.id:
                relevant_ids.add(event.id)

    # Keep a small causation closure so selected events can still be explained
    # without pulling the entire project event graph into a feature view.
    queue: deque[str] = deque(relevant_ids)
    while queue:
        event_id = queue.popleft()
        item = indexed.get(event_id)
        if item is None:
            continue
        _seq, event = item
        parent = str(event.causation_id or "")
        if parent and parent in indexed and parent not in relevant_ids:
            relevant_ids.add(parent)
            queue.append(parent)
        for _child_seq, child in children_by_parent.get(event_id, []):
            if not child.id or child.id in relevant_ids:
                continue
            if _event_matches_scope(child, scope):
                relevant_ids.add(child.id)
                queue.append(child.id)

    if not relevant_ids:
        return [], len(events)
    scoped = [(seq, event) for seq, event in events if event.id in relevant_ids]
    return scoped, len(events) - len(scoped)


def _event_scope(trace: dict[str, Any]) -> dict[str, set[str]]:
    task_ids: set[str] = set()
    fanout_ids: set[str] = set()
    run_ids: set[str] = set()
    event_ids: set[str] = set()
    feature_ids = {str(trace.get("feature_id") or ""), str((trace.get("target") or {}).get("id") or "")}
    trace_ids = {str(trace.get("trace_id") or ""), str((trace.get("target") or {}).get("trace_id") or "")}

    for node in ((trace.get("execution_graph") or {}).get("nodes") or []):
        if isinstance(node, dict):
            task_ids.add(str(node.get("task_id") or ""))
    for stage in ((trace.get("run_chain") or {}).get("stages") or []):
        if not isinstance(stage, dict):
            continue
        task_ids.update(_string_values(stage.get("task_ids")))
    for span in ((trace.get("trace") or {}).get("spans") or []):
        if not isinstance(span, dict):
            continue
        task_ids.add(str(span.get("task_id") or ""))
        fanout_ids.add(str(span.get("fanout_id") or ""))
        run_ids.add(str(span.get("run_id") or ""))
        event_ids.update(_string_values(span.get("raw_event_refs")))

    return {
        "feature_ids": {item for item in feature_ids if item},
        "trace_ids": {item for item in trace_ids if item},
        "task_ids": {item for item in task_ids if item},
        "fanout_ids": {item for item in fanout_ids if item},
        "run_ids": {item for item in run_ids if item},
        "event_ids": {item for item in event_ids if item},
    }


def _event_matches_scope(event: ZfEvent, scope: dict[str, set[str]]) -> bool:
    if event.id and event.id in scope["event_ids"]:
        return True
    data = payload(event)
    if _matches(scope["feature_ids"], [
        data.get("feature_id"),
        data.get("target_id"),
        data.get("pdd_id"),
        *(_string_values(data.get("feature_ids"))),
    ]):
        return True
    if _matches(scope["trace_ids"], [
        event.correlation_id,
        data.get("trace_id"),
        data.get("correlation_id"),
        *(_string_values(data.get("trace_ids"))),
    ]):
        return True
    if _matches(scope["task_ids"], [
        event.task_id,
        data.get("task_id"),
        *(_string_values(data.get("task_ids"))),
    ]):
        return True
    if _matches(scope["fanout_ids"], [
        data.get("fanout_id"),
        data.get("fanout_run_id"),
        *(_string_values(data.get("fanout_ids"))),
    ]):
        return True
    if _matches(scope["run_ids"], [data.get("run_id"), data.get("dispatch_id")]):
        return True
    return False


def _matches(allowed: set[str], values: list[object]) -> bool:
    if not allowed:
        return False
    return any(str(value or "") in allowed for value in values)


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def export_otlp_json(thick_trace: dict[str, Any]) -> dict[str, Any]:
    """Project a thick trace into a small OTLP-compatible JSON envelope."""

    spans = []
    for span in thick_trace.get("spans") or []:
        if not isinstance(span, dict):
            continue
        attrs = {
            "zaofu.project_id": thick_trace.get("project_id", ""),
            "zaofu.target_id": (thick_trace.get("target") or {}).get("id", ""),
            "zaofu.task_id": span.get("task_id", ""),
            "zaofu.run_id": span.get("run_id", ""),
            "zaofu.fanout_id": span.get("fanout_id", ""),
            "zaofu.role": span.get("role", ""),
            "zaofu.status": span.get("status", ""),
            "zaofu.kind": span.get("kind", ""),
            "zaofu.degraded": bool(span.get("degraded")),
        }
        spans.append({
            "trace_id": span.get("trace_id") or (thick_trace.get("target") or {}).get("trace_id", ""),
            "span_id": span.get("span_id", ""),
            "parent_span_id": span.get("parent_span_id", ""),
            "name": span.get("name") or span.get("span_id", ""),
            "start_time_unix_nano": _unix_nano(span.get("started_at")),
            "end_time_unix_nano": _unix_nano(span.get("ended_at") or span.get("started_at")),
            "attributes": attrs,
        })
    return {
        "resource_spans": [{
            "resource": {"attributes": {"service.name": "zaofu-delivery-trace"}},
            "scope_spans": [{
                "scope": {"name": "zaofu.delivery_thick_trace"},
                "spans": spans,
            }],
        }],
    }


def _graph(
    *,
    trace: dict[str, Any],
    events: EventSlice,
    behaviors: list[dict[str, Any]],
    evals: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def add_node(node: dict[str, Any]) -> None:
        node_id = str(node.get("id") or "")
        if node_id:
            nodes[node_id] = {**nodes.get(node_id, {}), **node}

    for node in ((trace.get("execution_graph") or {}).get("nodes") or []):
        task_id = str(node.get("task_id") or "")
        if not task_id:
            continue
        add_node({
            "id": f"task:{task_id}",
            "kind": "task",
            "label": str(node.get("title") or task_id),
            "status": str((node.get("actual") or {}).get("status") or ""),
            "task_id": task_id,
            "source_refs": _compact_refs(node),
            "behavior_ids": [],
            "eval_ids": [],
        })
    for edge in ((trace.get("execution_graph") or {}).get("edges") or []):
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if source and target:
            edges.append({
                "id": _edge_id("planned", source, target),
                "kind": str(edge.get("kind") or "planned"),
                "source": f"task:{source}",
                "target": f"task:{target}",
                "status": str(edge.get("status") or ""),
                "event_ids": [],
            })

    for stage in ((trace.get("run_chain") or {}).get("stages") or []):
        stage_id = str(stage.get("stage") or stage.get("stage_id") or "")
        if not stage_id:
            continue
        add_node({
            "id": f"stage:{stage_id}",
            "kind": "stage",
            "label": str(stage.get("label") or stage_id),
            "status": str(stage.get("status") or ""),
            "task_ids": list(stage.get("task_ids") or []),
        })
        for task_id in stage.get("task_ids") or []:
            edges.append({
                "id": _edge_id("contains", stage_id, str(task_id)),
                "kind": "contains",
                "source": f"stage:{stage_id}",
                "target": f"task:{task_id}",
                "status": str(stage.get("status") or ""),
                "event_ids": [],
            })

    for artifact in _artifacts(trace):
        ref = str(artifact.get("ref") or "")
        kind = str(artifact.get("kind") or "artifact")
        node_id = f"artifact:{_stable_id(kind, ref)}"
        add_node({
            "id": node_id,
            "kind": "artifact",
            "label": _tail(ref) or kind,
            "status": "available" if ref else "missing",
            "artifact_kind": kind,
            "ref": ref,
            "source_refs": {"ref": ref},
        })

    for span in ((trace.get("trace") or {}).get("spans") or [])[:80]:
        if not isinstance(span, dict):
            continue
        span_id = str(span.get("span_id") or "")
        if not span_id:
            continue
        task_id = str(span.get("task_id") or "")
        node_id = f"span:{span_id}"
        add_node({
            "id": node_id,
            "kind": "span",
            "label": str(span.get("name") or span.get("run_id") or span_id),
            "status": str(span.get("status") or ""),
            "task_id": task_id,
            "run_id": str(span.get("run_id") or ""),
            "fanout_id": str(span.get("fanout_id") or ""),
        })
        if task_id:
            nodes.setdefault(f"task:{task_id}", {
                "id": f"task:{task_id}", "kind": "task", "label": task_id,
                "status": "", "task_id": task_id, "behavior_ids": [], "eval_ids": [],
            })
            edges.append({
                "id": _edge_id("produced", task_id, span_id),
                "kind": "produced",
                "source": f"task:{task_id}",
                "target": node_id,
                "status": str(span.get("status") or ""),
                "event_ids": list(span.get("raw_event_refs") or []),
            })
        parent_id = str(span.get("parent_span_id") or "")
        if parent_id:
            edges.append({
                "id": _edge_id("caused_by", parent_id, span_id),
                "kind": "caused_by",
                "source": f"span:{parent_id}",
                "target": node_id,
                "status": str(span.get("status") or ""),
                "event_ids": list(span.get("raw_event_refs") or []),
            })

    for behavior in behaviors:
        node_id = f"behavior:{behavior['behavior_id']}"
        add_node({
            "id": node_id,
            "kind": "behavior",
            "label": behavior["kind"],
            "status": behavior["status"],
            "event_ids": behavior["event_ids"],
        })
        for task_id in behavior.get("task_ids") or []:
            task_node = nodes.setdefault(f"task:{task_id}", {
                "id": f"task:{task_id}", "kind": "task", "label": task_id,
                "status": "", "task_id": task_id, "behavior_ids": [], "eval_ids": [],
            })
            task_node.setdefault("behavior_ids", []).append(behavior["behavior_id"])
            edges.append({
                "id": _edge_id("failed_by", task_id, behavior["behavior_id"]),
                "kind": "failed_by",
                "source": f"task:{task_id}",
                "target": node_id,
                "status": behavior["status"],
                "event_ids": behavior["event_ids"],
            })

    for evaluation in evals:
        node_id = f"eval:{evaluation['eval_id']}"
        add_node({
            "id": node_id,
            "kind": "eval",
            "label": evaluation["kind"],
            "status": evaluation["status"],
            "event_ids": evaluation["event_ids"],
        })
        for task_id in evaluation.get("task_ids") or []:
            task_node = nodes.setdefault(f"task:{task_id}", {
                "id": f"task:{task_id}", "kind": "task", "label": task_id,
                "status": "", "task_id": task_id, "behavior_ids": [], "eval_ids": [],
            })
            task_node.setdefault("eval_ids", []).append(evaluation["eval_id"])
            edges.append({
                "id": _edge_id("validated_by", task_id, evaluation["eval_id"]),
                "kind": "validated_by",
                "source": f"task:{task_id}",
                "target": node_id,
                "status": evaluation["status"],
                "event_ids": evaluation["event_ids"],
            })

    graph_event_refs = _graph_event_refs(trace, behaviors, evals)
    for _seq, event in events:
        if not (
            (event.id and event.id in graph_event_refs)
            or (event.causation_id and event.causation_id in graph_event_refs)
        ):
            continue
        if event.causation_id and event.id:
            edges.append({
                "id": _edge_id("caused_by", event.causation_id, event.id),
                "kind": "caused_by",
                "source": f"event:{event.causation_id}",
                "target": f"event:{event.id}",
                "status": event_status(event),
                "event_ids": [event.causation_id, event.id],
            })

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": list(nodes.values()),
        "edges": edges,
        "layers": ["plan", "runtime", "gate", "behavior", "eval", "artifact"],
    }


def _graph_event_refs(
    trace: dict[str, Any],
    behaviors: list[dict[str, Any]],
    evals: list[dict[str, Any]],
) -> set[str]:
    refs: set[str] = set()
    for span in ((trace.get("trace") or {}).get("spans") or []):
        if isinstance(span, dict):
            refs.update(_string_values(span.get("raw_event_refs")))
    for row in [*behaviors, *evals]:
        refs.update(_string_values(row.get("event_ids")))
    return refs


def _behavior_overlay(events: EventSlice) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _seq, event in events:
        data = payload(event)
        task_id = str(event.task_id or data.get("task_id") or "")
        if event.type == "task.rework.triage.completed":
            classification = str(data.get("classification") or data.get("harness_classification") or "")
            if classification != "evidence_payload_gap":
                continue
            out.append(_behavior("missing_evidence", event, task_id, data, "failed"))
        elif event.type in {"worker.stuck", "worker.probe.silent"}:
            out.append(_behavior("worker_stuck", event, task_id, data, "failed" if event.type == "worker.stuck" else "warn"))
        elif event.type in _YELLOW_BEHAVIOR_EVENTS:
            out.append(_behavior(event.type.removeprefix("behavior.").removesuffix(".detected"), event, task_id, data, event_status(event)))
    return out


def _eval_overlay(events: EventSlice) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _seq, event in events:
        data = payload(event)
        if event.type.startswith(_P0_EVAL_PREFIXES):
            status = "passed" if event.type.endswith((".passed", ".skipped")) else "failed" if event.type.endswith(".failed") else event_status(event)
            out.append(_eval("functional_check", event, data, status))
        elif event.type in _YELLOW_EVAL_EVENTS:
            kind = event.type.removeprefix("eval.").removesuffix(".completed")
            if event.type == "replan.contract_eval.completed":
                kind = "replan_contract"
            out.append(_eval(kind, event, data, event_status(event)))
    return out


def _behavior(kind: str, event: ZfEvent, task_id: str, data: dict[str, Any], status: str) -> dict[str, Any]:
    behavior_id = _stable_id("behavior", kind, task_id, event.id)
    return {
        "behavior_id": behavior_id,
        "kind": kind,
        "status": status,
        "task_ids": [task_id] if task_id else [],
        "event_ids": [event.id] if event.id else [],
        "summary": str(data.get("reason") or data.get("summary") or kind),
        "owner_event_type": event.type,
        "detector": str(data.get("detector") or event.actor or ""),
    }


def _eval(kind: str, event: ZfEvent, data: dict[str, Any], status: str) -> dict[str, Any]:
    task_id = str(event.task_id or data.get("task_id") or "")
    return {
        "eval_id": _stable_id("eval", kind, task_id, event.id),
        "kind": kind,
        "status": status,
        "task_ids": [task_id] if task_id else [],
        "event_ids": [event.id] if event.id else [],
        "score": data.get("score"),
        "detail": data.get("detail") or data.get("reason") or data.get("failed_checks") or {},
        "evaluator": str(data.get("evaluator") or event.actor or ""),
        "owner_event_type": event.type,
    }


def _normalize_span(span: dict[str, Any]) -> dict[str, Any]:
    status = str(span.get("status") or "")
    kind = _span_kind(span)
    started = str(span.get("started_at") or "")
    ended = str(span.get("ended_at") or "")
    out = {
        **span,
        "kind": kind,
        "name": span.get("name") or span.get("run_id") or span.get("span_id") or kind,
        "cost_usd": _float(span.get("cost_usd") or span.get("total_cost_usd") or 0.0),
        "tool_calls": span.get("tool_calls") or [],
        "degraded": not started or not ended,
    }
    if status in {"failed", "blocked"} and not out.get("error"):
        out["error"] = {"status": status}
    return out


def _span_kind(span: dict[str, Any]) -> str:
    span_id = str(span.get("span_id") or "")
    if span_id.startswith("child:"):
        return "agent_run"
    if span_id.startswith("run:"):
        return "run_group"
    if span_id.startswith("event:"):
        return "event"
    role = str(span.get("role") or "")
    if role:
        return role
    return "runtime"


def _artifacts(trace: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    current_bundle = trace.get("current_bundle") if isinstance(trace.get("current_bundle"), dict) else {}
    for key in ("current_task_map_ref", "current_source_index_ref", "current_coverage_report_ref"):
        value = str(current_bundle.get(key) or trace.get(key) or "")
        if value:
            artifacts.append({"kind": key.removeprefix("current_").removesuffix("_ref"), "ref": value})
    task_map_ref = str((trace.get("task_map") or {}).get("task_map_ref") or "")
    if task_map_ref and not any(item.get("ref") == task_map_ref for item in artifacts):
        artifacts.append({"kind": "task_map", "ref": task_map_ref})
    return artifacts


def _improvement_candidates(behaviors: list[dict[str, Any]], evals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in [*behaviors, *evals]:
        status = str(item.get("status") or "")
        if status not in {"failed", "warn"}:
            continue
        kind = str(item.get("kind") or "unknown")
        task_id = ",".join(item.get("task_ids") or [])
        fingerprint = _stable_id("improve", kind, task_id)
        candidates.append({
            "candidate_id": f"improve:{fingerprint}",
            "kind": "backlog_candidate",
            "source_kind": kind,
            "status": "candidate",
            "fingerprint": fingerprint,
            "task_ids": item.get("task_ids") or [],
            "event_ids": item.get("event_ids") or [],
            "summary": f"Harden delivery flow for {kind}",
        })
    return candidates


def _diagnostics(
    trace: dict[str, Any],
    spans: list[dict[str, Any]],
    *,
    event_count: int = 0,
    excluded_event_count: int = 0,
) -> list[dict[str, str]]:
    diagnostics = list(trace.get("diagnostics") or [])
    if not spans:
        diagnostics.append({"kind": "thick_trace_no_spans", "message": "no delivery spans projected"})
    if excluded_event_count:
        diagnostics.append({
            "kind": "thick_trace_event_scope",
            "message": f"scoped {event_count - excluded_event_count}/{event_count} events to this delivery target",
        })
    return diagnostics


def _compact_refs(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    refs: dict[str, Any] = {}
    for key in ("task_id", "source_ref", "task_map_ref", "source_index_ref", "evidence_refs"):
        item = value.get(key)
        if item not in (None, "", []):
            refs[key] = item
    return refs


def _tail(value: str) -> str:
    if not value:
        return ""
    cleaned = value.rstrip("/")
    return cleaned.rsplit("/", 1)[-1] or cleaned


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stable_id(*parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _edge_id(kind: str, source: str, target: str) -> str:
    return f"edge:{kind}:{_stable_id(source, target)}"


def _unix_nano(ts: Any) -> int:
    if not ts:
        return 0
    try:
        from datetime import datetime

        text = str(ts)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return int(datetime.fromisoformat(text).timestamp() * 1_000_000_000)
    except Exception:
        return 0


def dumps_otlp_json(thick_trace: dict[str, Any]) -> str:
    return json.dumps(export_otlp_json(thick_trace), ensure_ascii=False, indent=2)
