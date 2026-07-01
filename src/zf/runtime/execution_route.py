"""Execution route projection.

The projection is derived from events only. It is intentionally read-only and
does not introduce a second task/workflow schema.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj


EventSlice = Sequence[tuple[int, ZfEvent]]

_STAGE_ORDER = ["plan", "dev", "review", "test", "gate", "done"]
_STAGE_LABELS = {
    "plan": "Plan",
    "dev": "Development",
    "review": "Review",
    "test": "Test",
    "gate": "Gate",
    "done": "Done",
}
_TERMINAL_DONE_TYPES = {
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "review.approved",
    "test.passed",
    "judge.passed",
    "task.done",
    "task.done.accepted",
    "candidate.ready",
    "ship.completed",
}
_RUNNING_TYPES = {
    "task.dispatched",
    "worker.progress",
    "worker.heartbeat",
    "fanout.started",
    "fanout.child.dispatched",
}
_ROLE_STAGE_PREFIXES = {
    "arch": "plan",
    "architect": "plan",
    "plan": "plan",
    "planner": "plan",
    "lead": "plan",
    "dev": "dev",
    "developer": "dev",
    "review": "review",
    "reviewer": "review",
    "critic": "review",
    "qa": "test",
    "test": "test",
    "tester": "test",
    "verify": "test",
    "verifier": "test",
    "judge": "gate",
    "gate": "gate",
    "discriminator": "gate",
}
_PHASE_STAGE = {
    "intake": "plan",
    "design": "plan",
    "design_done": "plan",
    "design_critiqued": "review",
    "implement": "dev",
    "build_done": "dev",
    "review": "review",
    "review_approved": "review",
    "test": "test",
    "test_passed": "test",
    "judge": "gate",
    "judge_passed": "gate",
    "done": "done",
}


def project_execution_route(
    events: EventSlice,
    *,
    task_id: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    """Project actual execution route as linear stages plus a compact DAG."""

    stage_nodes: dict[str, dict[str, dict[str, Any]]] = {
        stage: {} for stage in _STAGE_ORDER
    }
    source_events: list[dict[str, Any]] = []
    for seq, event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        stage = _stage_for_event(event, payload)
        if not stage:
            continue
        actor = _actor_for_event(event, payload, stage)
        node_key = f"{stage}:{actor or 'system'}"
        node = stage_nodes[stage].setdefault(
            node_key,
            {
                "id": _node_id(stage, actor),
                "stage": stage,
                "stage_label": _STAGE_LABELS[stage],
                "actor": actor or "system",
                "role": _role_prefix(actor),
                "status": "observed",
                "first_seq": seq,
                "last_seq": seq,
                "first_ts": event.ts,
                "last_ts": event.ts,
                "event_count": 0,
                "event_types": [],
                "task_ids": set(),
                "evidence_event_ids": [],
                "failed_count": 0,
            },
        )
        event_status = _event_status(event, payload)
        node["last_seq"] = seq
        node["last_ts"] = event.ts
        node["event_count"] += 1
        if event.type not in node["event_types"]:
            node["event_types"].append(event.type)
        if event.task_id:
            node["task_ids"].add(event.task_id)
        if event.id:
            node["evidence_event_ids"].append(event.id)
        if event_status == "failed":
            node["failed_count"] += 1
        if event_status != "observed":
            node["status"] = event_status
        source_events.append({
            "seq": seq,
            "event_id": event.id,
            "type": event.type,
            "actor": event.actor or "",
            "task_id": event.task_id or "",
            "stage": stage,
        })

    linear = [
        _stage_projection(stage, list(stage_nodes[stage].values()))
        for stage in _STAGE_ORDER
        if stage_nodes[stage]
    ]
    nodes = [
        _node_projection(node)
        for stage in _STAGE_ORDER
        for node in sorted(
            stage_nodes[stage].values(),
            key=lambda item: (int(item["first_seq"]), str(item["actor"])),
        )
    ]
    edges = _route_edges(linear, nodes)
    swimlanes = _swimlanes(nodes)
    summary_steps = [
        _summary_label(step)
        for step in linear
        if _summary_label(step)
    ]
    status = _route_status(linear)
    current = linear[-1] if linear else {}
    result = {
        "schema_version": "execution-route.v1",
        "scope": {
            "task_id": task_id,
            "trace_id": trace_id,
        },
        "status": status,
        "current_stage": current.get("stage", ""),
        "current_stage_label": current.get("label", ""),
        "summary": " -> ".join(summary_steps),
        "step_count": len(linear),
        "parallel": any(bool(step.get("parallel")) for step in linear),
        "linear": linear,
        "dag": {
            "nodes": nodes,
            "edges": edges,
        },
        "swimlanes": swimlanes,
        "source_event_count": len(source_events),
        "source_events": source_events[-80:],
        "empty": not linear,
    }
    return redact_obj(result)


def project_route_summary(events: EventSlice, *, task_id: str = "") -> dict[str, Any]:
    route = project_execution_route(events, task_id=task_id)
    return {
        "schema_version": route["schema_version"],
        "summary": route["summary"],
        "status": route["status"],
        "current_stage": route["current_stage"],
        "current_stage_label": route["current_stage_label"],
        "step_count": len(route["linear"]),
        "parallel": any(bool(step.get("parallel")) for step in route["linear"]),
        "empty": route["empty"],
    }


def _stage_for_event(event: ZfEvent, payload: dict[str, Any]) -> str:
    event_type = str(event.type or "")
    if event_type in {"task.done", "task.done.accepted"}:
        return "done"
    if event_type == "task.status_changed" and _payload_str(payload, "to") == "done":
        return "done"
    phase_stage = _PHASE_STAGE.get(_payload_str(payload, "phase"))
    if phase_stage:
        return phase_stage
    if event_type == "task.dispatched":
        return _stage_for_role(
            _payload_str(payload, "assignee")
            or _payload_str(payload, "role")
            or _payload_str(payload, "instance_id")
            or str(event.actor or "")
        ) or "dev"
    if event_type.startswith(("arch.", "design.", "workflow.invoke.", "task.created")):
        return "plan"
    if event_type.startswith(("dev.", "fanout.child.")):
        return "dev"
    if event_type.startswith(("review.", "critic.")):
        return "review"
    if event_type.startswith(("test.", "verify.")):
        return "test"
    if event_type.startswith(("judge.", "gate.", "discriminator.")):
        return "gate"
    if event_type.startswith("fanout."):
        return "dev"
    if event_type.startswith("worker."):
        return _stage_for_role(
            _payload_str(payload, "role")
            or _payload_str(payload, "instance_id")
            or str(event.actor or "")
        )
    if event_type.startswith("candidate.") or event_type.startswith("ship."):
        return "done"
    return ""


def _actor_for_event(event: ZfEvent, payload: dict[str, Any], stage: str) -> str:
    values = []
    if event.type == "task.dispatched":
        values.append(_payload_str(payload, "assignee"))
    values.extend([
        _payload_str(payload, "instance_id"),
        _payload_str(payload, "role_instance"),
        _payload_str(payload, "child_run"),
        _payload_str(payload, "child_id"),
        _payload_str(payload, "role"),
        str(event.actor or ""),
    ])
    actor = next((value for value in values if value), "")
    if actor:
        return actor
    if stage == "done":
        return "kernel"
    return stage


def _event_status(event: ZfEvent, payload: dict[str, Any]) -> str:
    event_type = str(event.type or "")
    explicit = _payload_str(payload, "status").lower()
    if explicit in {"failed", "rejected", "blocked", "timed_out", "cancelled"}:
        return "blocked" if explicit == "timed_out" else explicit
    if explicit in {"done", "passed", "approved", "completed", "accepted", "ready"}:
        return "done"
    if event_type.endswith((".failed", ".rejected")):
        return "failed"
    if event_type.endswith((".blocked", ".timed_out")):
        return "blocked"
    if event_type in _TERMINAL_DONE_TYPES or event_type.endswith((
        ".passed",
        ".approved",
        ".completed",
        ".accepted",
    )):
        return "done"
    if event_type in _RUNNING_TYPES or event_type.endswith((".started", ".running")):
        return "running"
    if event_type.endswith((".requested", ".created", ".proposed")):
        return "done"
    return "observed"


def _stage_for_role(role: str) -> str:
    prefix = _role_prefix(role)
    return _ROLE_STAGE_PREFIXES.get(prefix, "")


def _role_prefix(value: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    for sep in ("-", "_", ".", ":"):
        token = token.split(sep, 1)[0]
    return token


def _stage_projection(stage: str, raw_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = sorted(raw_nodes, key=lambda item: (int(item["first_seq"]), str(item["actor"])))
    statuses = [str(node.get("status") or "observed") for node in nodes]
    actors = [str(node.get("actor") or "") for node in nodes if node.get("actor")]
    event_types: list[str] = []
    task_ids: set[str] = set()
    for node in nodes:
        for event_type in node.get("event_types", []):
            if event_type not in event_types:
                event_types.append(str(event_type))
        task_ids.update(str(item) for item in node.get("task_ids", set()) if item)
    return {
        "stage": stage,
        "label": "Dev Fanout" if stage == "dev" and len(nodes) > 1 else _STAGE_LABELS[stage],
        "status": _combine_statuses(statuses),
        "parallel": len(nodes) > 1,
        "actors": actors,
        "node_ids": [str(node["id"]) for node in nodes],
        "first_seq": min(int(node["first_seq"]) for node in nodes),
        "last_seq": max(int(node["last_seq"]) for node in nodes),
        "first_ts": min(str(node["first_ts"]) for node in nodes),
        "last_ts": max(str(node["last_ts"]) for node in nodes),
        "event_count": sum(int(node["event_count"]) for node in nodes),
        "event_types": event_types,
        "task_ids": sorted(task_ids),
        "failed_count": sum(int(node.get("failed_count") or 0) for node in nodes),
    }


def _node_projection(node: dict[str, Any]) -> dict[str, Any]:
    out = dict(node)
    out["task_ids"] = sorted(str(item) for item in out.get("task_ids", set()) if item)
    out["evidence_event_ids"] = list(out.get("evidence_event_ids", []))[-8:]
    return out


def _route_edges(linear: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    node_ids = {str(node["id"]) for node in nodes}
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for prev, current in zip(linear, linear[1:]):
        for source in prev.get("node_ids", []):
            for target in current.get("node_ids", []):
                if source not in node_ids or target not in node_ids:
                    continue
                key = (str(source), str(target))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "from": str(source),
                    "to": str(target),
                    "kind": "actual_route",
                })
    return edges


def _swimlanes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        actor = str(node.get("actor") or "system")
        grouped.setdefault(actor, []).append({
            "node_id": node["id"],
            "stage": node["stage"],
            "status": node["status"],
            "first_ts": node["first_ts"],
            "last_ts": node["last_ts"],
            "event_count": node["event_count"],
        })
    return [
        {
            "actor": actor,
            "items": sorted(items, key=lambda item: str(item["first_ts"])),
        }
        for actor, items in sorted(grouped.items())
    ]


def _combine_statuses(statuses: list[str]) -> str:
    if not statuses:
        return "observed"
    if any(status == "running" for status in statuses):
        return "running"
    latest = statuses[-1]
    if latest in {"done", "failed", "blocked", "cancelled"}:
        return latest
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "blocked" for status in statuses):
        return "blocked"
    if any(status == "done" for status in statuses):
        return "done"
    return latest


def _route_status(linear: list[dict[str, Any]]) -> str:
    if not linear:
        return "empty"
    if linear[-1]["stage"] == "done" and linear[-1]["status"] in {"done", "observed"}:
        return "done"
    last_status = str(linear[-1].get("status") or "")
    if last_status in {"failed", "blocked", "running"}:
        return last_status
    if any(step.get("status") == "running" for step in linear):
        return "running"
    if any(step.get("status") == "failed" for step in linear):
        return "failed"
    if any(step.get("status") == "blocked" for step in linear):
        return "blocked"
    return "observed"


def _summary_label(step: dict[str, Any]) -> str:
    actors = [str(actor) for actor in step.get("actors", []) if actor]
    if step.get("stage") == "done":
        return "done"
    if actors:
        return "/".join(actors[:4]) + ("+" if len(actors) > 4 else "")
    return str(step.get("label") or step.get("stage") or "")


def _node_id(stage: str, actor: str) -> str:
    safe_actor = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in (actor or "system"))
    return f"{stage}:{safe_actor or 'system'}"


def _payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if value is not None else ""
