"""Fanout child aggregation helpers for ``workflow_trace``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_run import build_workflow_run

EventSlice = list[tuple[int, ZfEvent]]


def build_fanout_runs(events: EventSlice) -> list[dict[str, Any]]:
    fanout_ids = sorted({
        str(_payload(event).get("fanout_id") or "")
        for _seq, event in events
        if str(_payload(event).get("fanout_id") or "")
    })
    runs: list[dict[str, Any]] = []
    for fanout_id in fanout_ids:
        run = build_workflow_run(fanout_id=fanout_id, events=events)
        fanout_events = [
            (seq, event) for seq, event in events
            if str(_payload(event).get("fanout_id") or "") == fanout_id
        ]
        children = _child_runs(fanout_events)
        run["child_runs"] = strip_internal_child_events(children)
        metrics = fanout_metrics(children)
        metrics["aggregate_wait_ms"] = _aggregate_wait_ms(fanout_events)
        run["metrics"] = metrics
        runs.append(run)
    return runs


def fanouts_by_stage(fanout_runs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for run in fanout_runs:
        stage_id = _base_stage_id(str(run.get("stage_id") or ""))
        if stage_id:
            out.setdefault(stage_id, []).append(run)
    return out


def fanout_stage_status(fanouts: list[dict[str, Any]]) -> str:
    if not fanouts:
        return ""
    status = str(fanouts[-1].get("status") or "")
    if status == "completed":
        return "passed"
    if status in {"timed_out", "cancelled"}:
        return "failed"
    if status == "aggregating":
        return "aggregating"
    if status in {"running", "requested", "recorded_no_runtime"}:
        return "running"
    return ""


def fanout_metrics(children: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "children_total": len(children),
        "children_running": len([c for c in children if c.get("status") == "running"]),
        "children_passed": len([c for c in children if c.get("status") == "passed"]),
        "children_failed": len([c for c in children if c.get("status") == "failed"]),
        "children_pending": len([c for c in children if c.get("status") == "pending"]),
    }


def strip_internal_child_events(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for child in children:
        item = dict(child)
        item.pop("_event", None)
        item.pop("_last_seq", None)
        clean.append(item)
    return clean


def _child_runs(events: EventSlice) -> list[dict[str, Any]]:
    children: dict[str, dict[str, Any]] = {}
    for seq, event in events:
        payload = _payload(event)
        if event.type == "fanout.started":
            _record_expected_children(children, event, seq)
        if not event.type.startswith("fanout.child."):
            continue
        child_id = str(payload.get("child_id") or event.task_id or "")
        if not child_id:
            continue
        child = children.setdefault(child_id, _new_child(child_id, event, seq))
        child["_last_seq"] = seq
        child["source_event_ids"].append(event.id)
        child["role"] = child["role"] or str(payload.get("role") or "")
        child["worker_id"] = child["worker_id"] or str(
            payload.get("worker_id") or payload.get("role_instance") or ""
        )
        child["task_id"] = child["task_id"] or str(event.task_id or payload.get("task_id") or "")
        if event.type == "fanout.child.dispatched":
            child["status"] = "running"
            child["started_at"] = child["started_at"] or event.ts
        elif event.type == "fanout.child.completed":
            child["status"] = "passed"
            child["ended_at"] = event.ts
        elif event.type == "fanout.child.failed":
            child["status"] = "failed"
            child["ended_at"] = event.ts
            child["error"] = {
                "type": str(payload.get("error_type") or payload.get("type") or ""),
                "message": str(payload.get("reason") or payload.get("message") or ""),
            }
            child["failure_event_id"] = event.id
            child["failure_reason"] = str(payload.get("reason") or payload.get("message") or "")
            child["_event"] = event
    for child in children.values():
        child["duration_ms"] = _duration_ms(
            child.get("started_at") or "",
            child.get("ended_at") or "",
        )
    return list(children.values())


def _record_expected_children(children: dict[str, dict[str, Any]], event: ZfEvent, seq: int) -> None:
    for child in _payload(event).get("expected_children") or []:
        if not isinstance(child, dict):
            continue
        child_id = str(child.get("child_id") or "")
        if not child_id:
            continue
        item = children.setdefault(child_id, _new_child(child_id, event, seq))
        item.update({
            "role": str(child.get("role") or item.get("role") or ""),
            "worker_id": str(child.get("role_instance") or item.get("worker_id") or ""),
            "task_id": str(child.get("task_id") or item.get("task_id") or ""),
        })


def _new_child(child_id: str, event: ZfEvent, seq: int) -> dict[str, Any]:
    return {
        "child_id": child_id,
        "stage_id": str(_payload(event).get("stage_id") or ""),
        "role": "",
        "task_id": str(event.task_id or _payload(event).get("task_id") or ""),
        "status": "pending",
        "worker_id": "",
        "started_at": "",
        "ended_at": "",
        "duration_ms": None,
        "error": {},
        "links": {"events": str(event.correlation_id or "")},
        "source_event_ids": [event.id] if event.id else [],
        "_last_seq": seq,
    }


def _duration_ms(started_at: str, ended_at: str) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((ended - started).total_seconds() * 1000))


def _aggregate_wait_ms(events: EventSlice) -> int | None:
    child_terminal = [
        event.ts
        for _seq, event in events
        if event.type in {"fanout.child.completed", "fanout.child.failed"}
    ]
    if not child_terminal:
        return None
    aggregate_terminal = [
        event.ts
        for _seq, event in events
        if event.type in {
            "fanout.aggregate.completed",
            "fanout.synth.completed",
            "fanout.aggregate.started",
        }
    ]
    if not aggregate_terminal:
        return None
    return _duration_ms(max(child_terminal), aggregate_terminal[-1])


def _base_stage_id(stage_id: str) -> str:
    return stage_id.split(":", 1)[0]


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None:
        return {}
    return event.payload if isinstance(event.payload, dict) else {}
