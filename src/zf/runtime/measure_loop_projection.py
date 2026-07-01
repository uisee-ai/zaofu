"""Measure Loop projection.

``measure-loop.v1`` is a read-only, product-delivery-facing projection. It
keeps the existing ``loop.v1`` projection focused on behavior/eval/improvement
details while exposing lens-scoped process metrics for the Measure / Loop page.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.delivery_projection_common import event_status, payload
from zf.runtime.dispatch_diagnostics import build_dispatch_diagnostics
from zf.runtime.loop_projection import build_loop_projection

EventSlice = Sequence[tuple[int, ZfEvent]]

LENSES = [
    {"id": "all", "label": "All", "default_layout": "ring"},
    {"id": "agent", "label": "Agent", "default_layout": "star"},
    {"id": "verification", "label": "Verification", "default_layout": "ring"},
    {"id": "event_driven", "label": "Event-driven", "default_layout": "dag"},
    {"id": "hill_climbing", "label": "Hill-climbing", "default_layout": "ring"},
]

_DONE = {"done", "passed", "approved", "completed", "shipped"}
_ACTIVE = {
    "in_progress",
    "running",
    "review",
    "in_review",
    "verify",
    "testing",
    "test",
    "judge",
    "dispatched",
}
_BLOCKED = {"blocked", "failed", "error"}
_READY = {"backlog", "ready", "todo"}
_VERIFY_PREFIXES = ("review.", "test.", "judge.", "gate.", "static_gate.", "meta_gate.", "discriminator.")
_HILL_PREFIXES = ("replan.", "autoresearch.", "loop.learning.", "loop.action.", "spine_review.")
_REF_LIMIT = 80


def build_measure_loop_projection(
    state_dir: Path,
    *,
    config: Any | None = None,
    project_root: Path | None = None,
    project_id: str = "",
    feature_id: str = "",
    lens: str = "all",
    generated_at: str | None = None,
    events: EventSlice | None = None,
    loop_projection: dict[str, Any] | None = None,
    dispatch_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a lens-scoped process projection for Measure / Loop."""

    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    lens = _normalize_lens(lens)
    events = list(events) if events is not None else _read_events(state_dir, config=config)
    active_tasks = _read_tasks(state_dir, include_archive=False)
    all_tasks = _read_tasks(state_dir, include_archive=True)
    scoped_tasks = _filter_tasks(all_tasks, feature_id)
    current_tasks = _filter_tasks(active_tasks, feature_id)
    loop_projection = loop_projection or build_loop_projection(
        events=events,
        generated_at=generated_at,
        project_id=project_id,
    )
    dispatch_diagnostics = dispatch_diagnostics or build_dispatch_diagnostics(
        state_dir,
        config=config,
        project_root=project_root,
    )
    ctx = {
        "events": _filter_events(events, feature_id),
        "all_events": events,
        "tasks": scoped_tasks,
        "current_tasks": current_tasks,
        "ready_ids": _ready_ids(state_dir),
        "loop_projection": loop_projection,
        "dispatch": dispatch_diagnostics,
        "state_dir": state_dir,
        "feature_id": feature_id,
    }
    metrics, stages, graph = _lens_payload(lens, ctx)
    result = {
        "schema_version": "measure-loop.v1",
        "generated_at": generated_at,
        "project_id": project_id,
        "feature_id": feature_id,
        "active_lens": lens,
        "lenses": LENSES,
        "summary": _summary(ctx),
        "metrics": metrics,
        "stages": stages,
        "graph": graph,
        "feed": _feed(ctx["events"], lens),
        "diagnostics": _diagnostics(ctx),
        "source_projection_refs": [
            "TaskStore",
            "EventLog",
            "dispatch-diagnostics.v1",
            "loop.v1",
        ],
    }
    return redact_obj(result)


def _read_events(state_dir: Path, *, config: Any | None) -> list[tuple[int, ZfEvent]]:
    try:
        return list(enumerate(event_log_from_project(state_dir, config=config).read_all()))
    except Exception:
        return []


def _read_tasks(state_dir: Path, *, include_archive: bool) -> list[Task]:
    try:
        store = TaskStore(state_dir / "kanban.json")
        return store.list_all_with_archive() if include_archive else store.list_all()
    except Exception:
        return []


def _ready_ids(state_dir: Path) -> set[str]:
    try:
        return {task.id for task in TaskStore(state_dir / "kanban.json").ready()}
    except Exception:
        return set()


def _normalize_lens(value: str) -> str:
    allowed = {item["id"] for item in LENSES}
    return value if value in allowed else "all"


def _filter_tasks(tasks: Iterable[Task], feature_id: str) -> list[Task]:
    if not feature_id:
        return list(tasks)
    return [task for task in tasks if getattr(task.contract, "feature_id", "") == feature_id]


def _filter_events(events: EventSlice, feature_id: str) -> list[tuple[int, ZfEvent]]:
    if not feature_id:
        return list(events)
    out: list[tuple[int, ZfEvent]] = []
    for item in events:
        event = item[1]
        data = payload(event)
        if feature_id in {
            str(data.get("feature_id") or ""),
            str(data.get("target_id") or ""),
            str(data.get("trace_id") or ""),
            str(event.correlation_id or ""),
            *[str(value) for value in data.get("feature_ids", []) if str(value)],
            *[str(value) for value in data.get("trace_ids", []) if str(value)],
        }:
            out.append(item)
            continue
        task_ids = {str(data.get("task_id") or ""), str(event.task_id or "")}
        if any(task_id.startswith(feature_id) for task_id in task_ids if task_id):
            out.append(item)
    return out


def _task_counts(tasks: list[Task], current_tasks: list[Task], ready_ids: set[str]) -> dict[str, int]:
    total = len(tasks)
    done = sum(1 for task in tasks if task.status in _DONE)
    active = sum(1 for task in current_tasks if task.status in _ACTIVE)
    blocked = sum(1 for task in current_tasks if task.status in _BLOCKED or task.blocked_by)
    ready = sum(1 for task in current_tasks if task.id in ready_ids or task.status in _READY)
    waiting = max(0, total - done - active - blocked)
    return {
        "total": total,
        "done": done,
        "active": active,
        "blocked": blocked,
        "ready": ready,
        "waiting": waiting,
    }


def _verification_counts(events: EventSlice) -> dict[str, int]:
    total = passed = failed = missing = rework = 0
    for _seq, event in events:
        if event.type.startswith(_VERIFY_PREFIXES):
            total += 1
            status = event_status(event)
            if status == "failed":
                failed += 1
            elif status in {"done", "passed"}:
                passed += 1
        if (
            "missing" in str(payload(event).get("reason") or "").lower()
            or ("evidence" in event.type and event_status(event) == "failed")
        ):
            missing += 1
        if event.type.startswith("task.rework."):
            rework += 1
    return {"total": total, "passed": passed, "failed": failed, "missing": missing, "rework": rework}


def _dataset_spine(ctx: dict[str, Any]) -> dict[str, Any]:
    """Provisional eval-dataset spine (design 101 §3 learn).

    Prefers REAL captured regression cases (design 101 §8 C); only when
    none exist does it fall back to a failure/rework proxy (provisional).
    ``recovered`` are cases whose source task is now done."""
    done_ids = {task.id for task in ctx["tasks"] if task.status in _DONE}
    scoped_ids = {getattr(task, "id", "") for task in ctx["tasks"]}
    feature_id = str(ctx.get("feature_id") or "")
    # I1a — real captured regression cases take precedence over the proxy.
    state_dir = ctx.get("state_dir")
    if state_dir is not None:
        try:
            from zf.runtime.regression_case import list_regression_cases

            real = list_regression_cases(state_dir)
        except Exception:
            real = []
        if feature_id:
            real = [c for c in real if not c.feature_id or c.feature_id == feature_id]
        if real:
            cases = len(real)
            recovered = sum(1 for c in real if c.source_task_id in done_ids)
            return {
                "cases": cases,
                "recovered": recovered,
                "pass_rate": round(recovered / cases, 3) if cases else None,
                "provisional": False,
            }
    # I6 — proxy: count failure/rework events whose task belongs to the
    # feature (by task_id), not only events carrying feature_id.
    failure_task_ids: set[str] = set()
    for _seq, event in ctx.get("all_events") or ctx["events"]:
        etype = str(getattr(event, "type", "") or "")
        if "rework" in etype or etype.endswith(".failed") or etype.endswith(".rejected"):
            tid = str(getattr(event, "task_id", "") or "")
            if tid and (not feature_id or tid in scoped_ids):
                failure_task_ids.add(tid)
    cases = len(failure_task_ids)
    recovered = len(failure_task_ids & done_ids)
    return {
        "cases": cases,
        "recovered": recovered,
        "pass_rate": round(recovered / cases, 3) if cases else None,
        "provisional": True,
    }


def _summary(ctx: dict[str, Any]) -> dict[str, Any]:
    tasks = _task_counts(ctx["tasks"], ctx["current_tasks"], ctx["ready_ids"])
    verify = _verification_counts(ctx["events"])
    workers = ctx["dispatch"].get("worker_availability", []) or []
    active_agents = sum(1 for worker in workers if worker.get("active_task_id"))
    idle_ready = len(ctx["dispatch"].get("notifications", []) or [])
    return {
        **tasks,
        "delivery_percent": _percent(tasks["done"], tasks["total"]),
        "active_agents": active_agents,
        "idle_ready": idle_ready,
        "gate_pass_percent": _percent(verify["passed"], verify["total"]),
        "gate_failed": verify["failed"],
        "rework": verify["rework"],
        "loop_total": int((ctx["loop_projection"].get("summary") or {}).get("total") or 0),
        "dataset": _dataset_spine(ctx),
    }


def _lens_payload(lens: str, ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    builders = {
        "agent": _agent_payload,
        "verification": _verification_payload,
        "event_driven": _event_payload,
        "hill_climbing": _hill_payload,
    }
    return builders.get(lens, _all_payload)(ctx)


def _all_payload(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    summary = _summary(ctx)
    ready_tasks = _tasks_with_state(ctx["current_tasks"], ctx["ready_ids"], _READY)
    active_tasks = _tasks_with_state(ctx["current_tasks"], ctx["ready_ids"], _ACTIVE)
    blocked_tasks = [
        task for task in ctx["current_tasks"]
        if task.status in _BLOCKED or task.blocked_by
    ]
    done_tasks = [task for task in ctx["tasks"] if task.status in _DONE]
    verify_events = _events_with_prefix(ctx["events"], _VERIFY_PREFIXES)
    rework_events = _events_with_prefix(ctx["events"], ("task.rework.",))
    dispatch_events = _events_with_type(ctx["events"], ("task.dispatched",))
    metrics = [
        _metric("delivery", "Delivery", f"{summary['delivery_percent']}%", summary["delivery_percent"], "info", f"{summary['done']}/{summary['total']} tasks done", refs=_lineage(tasks=ctx["tasks"], events=ctx["events"], graph_node_ids=["plan", "work", "verify", "rework_ship"])),
        _metric("ready", "Ready", summary["ready"], summary["ready"], "warn" if summary["ready"] else "muted", "ready task queue", refs=_lineage(tasks=ready_tasks, graph_node_ids=["dispatch"])),
        _metric("active", "Active", summary["active_agents"], summary["active_agents"], "info", "agents with active tasks", refs=_lineage(tasks=active_tasks, graph_node_ids=["work"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("idle_ready", "Idle-Ready", summary["idle_ready"], summary["idle_ready"], "warn" if summary["idle_ready"] else "ok", "dispatch why-not signals", refs=_lineage(graph_node_ids=["dispatch"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("blocked", "Blocked", summary["blocked"], summary["blocked"], "err" if summary["blocked"] else "ok", "blocked current tasks", refs=_lineage(tasks=blocked_tasks, graph_node_ids=["work"])),
        _metric("gate_pass", "Gate Pass", f"{summary['gate_pass_percent']}%", summary["gate_pass_percent"], "ok" if summary["gate_pass_percent"] >= 70 else "warn", "verification pass ratio", refs=_lineage(events=verify_events, graph_node_ids=["verify"])),
    ]
    stages = [
        _stage("plan", "Plan", f"{summary['total']} tasks", f"{summary['waiting']} waiting", "info", refs=_lineage(tasks=ctx["tasks"], graph_node_ids=["plan"])),
        _stage("dispatch", "Dispatch", f"{summary['ready']} ready", f"{summary['idle_ready']} why-not", "warn" if summary["idle_ready"] else "ok", refs=_lineage(tasks=ready_tasks, events=dispatch_events, graph_node_ids=["dispatch"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _stage("work", "Work", f"{summary['active_agents']} active", f"{summary['active']} in progress", "info", refs=_lineage(tasks=active_tasks, graph_node_ids=["work"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _stage("verify", "Verify", f"{summary['gate_pass_percent']}% pass", f"{summary['gate_failed']} failed", "warn" if summary["gate_failed"] else "ok", refs=_lineage(events=verify_events, graph_node_ids=["verify"])),
        _stage("rework_ship", "Rework/Ship", f"{summary['rework']} rework", f"{summary['done']} shipped/done", "warn" if summary["rework"] else "ok", refs=_lineage(tasks=done_tasks, events=rework_events, graph_node_ids=["rework_ship"])),
    ]
    return metrics, stages, _graph("ring", stages)


def _agent_payload(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    workers = ctx["dispatch"].get("worker_availability", []) or []
    dispatchable = ctx["dispatch"].get("dispatchable_worker_count", 0)
    stale = sum(1 for worker in workers if worker.get("stale"))
    active = sum(1 for worker in workers if worker.get("active_task_id"))
    worker_task_ids = [str(worker.get("active_task_id") or "") for worker in workers]
    stale_task_ids = [str(worker.get("active_task_id") or "") for worker in workers if worker.get("stale")]
    worker_events = _events_with_prefix(ctx["events"], ("worker.",))
    metrics = [
        _metric("active_agents", "Active Agents", active, active, "info", "workers carrying active tasks", refs=_lineage(task_ids=worker_task_ids, graph_node_ids=["work"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("dispatchable", "Dispatchable", dispatchable, dispatchable, "ok" if dispatchable else "muted", "idle workers available", refs=_lineage(graph_node_ids=["briefing"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("stale", "Stale", stale, stale, "err" if stale else "ok", "stale worker availability", refs=_lineage(task_ids=stale_task_ids, events=worker_events, graph_node_ids=["heartbeat"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("workers", "Workers", len(workers), len(workers), "info", "known worker sessions", refs=_lineage(events=worker_events, graph_node_ids=["briefing", "work", "heartbeat"], source_projection_refs=["dispatch-diagnostics.v1"])),
    ]
    stages = [
        _stage("briefing", "Briefing", str(len(ctx["current_tasks"])), "current tasks", "info", refs=_lineage(tasks=ctx["current_tasks"], graph_node_ids=["briefing"])),
        _stage("work", "Work", str(active), "active workers", "info", refs=_lineage(task_ids=worker_task_ids, graph_node_ids=["work"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _stage("heartbeat", "Heartbeat", str(max(0, len(workers) - stale)), f"{stale} stale", "warn" if stale else "ok", refs=_lineage(task_ids=stale_task_ids or worker_task_ids, events=worker_events, graph_node_ids=["heartbeat"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _stage("complete", "Complete/Emit", str(_summary(ctx)["done"]), "done tasks", "ok", refs=_lineage(tasks=[task for task in ctx["tasks"] if task.status in _DONE], graph_node_ids=["complete"])),
    ]
    return metrics, stages, _graph("star", stages)


def _verification_payload(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    verify = _verification_counts(ctx["events"])
    pass_percent = _percent(verify["passed"], verify["total"])
    verify_events = _events_with_prefix(ctx["events"], _VERIFY_PREFIXES)
    failed_events = [item for item in verify_events if event_status(item[1]) == "failed"]
    missing_events = [
        item for item in ctx["events"]
        if "missing" in str(payload(item[1]).get("reason") or "").lower()
        or ("evidence" in item[1].type and event_status(item[1]) == "failed")
    ]
    rework_events = _events_with_prefix(ctx["events"], ("task.rework.",))
    metrics = [
        _metric("gate_pass", "Gate Pass", f"{pass_percent}%", pass_percent, "ok" if pass_percent >= 70 else "warn", "passed verification events", refs=_lineage(events=verify_events, graph_node_ids=["dev_done", "review", "test", "judge"])),
        _metric("failed", "Failed Gates", verify["failed"], verify["failed"], "err" if verify["failed"] else "ok", "failed verification events", refs=_lineage(events=failed_events, graph_node_ids=["review", "test", "judge"])),
        _metric("missing_evidence", "Missing Evidence", verify["missing"], verify["missing"], "err" if verify["missing"] else "ok", "missing evidence signals", refs=_lineage(events=missing_events, graph_node_ids=["judge", "done_rework"])),
        _metric("rework", "Rework", verify["rework"], verify["rework"], "warn" if verify["rework"] else "muted", "rework events", refs=_lineage(events=rework_events, graph_node_ids=["done_rework"])),
    ]
    stages = [
        _stage("dev_done", "Dev Done", str(_event_count(ctx["events"], "dev.build.done")), "build complete", "info", refs=_lineage(events=_events_with_type(ctx["events"], ("dev.build.done",)), graph_node_ids=["dev_done"])),
        _stage("review", "Review", str(_prefix_count(ctx["events"], "review.")), "review events", "info", refs=_lineage(events=_events_with_prefix(ctx["events"], ("review.",)), graph_node_ids=["review"])),
        _stage("test", "Test", str(_prefix_count(ctx["events"], "test.")), "test events", "info", refs=_lineage(events=_events_with_prefix(ctx["events"], ("test.",)), graph_node_ids=["test"])),
        _stage("judge", "Judge", str(_prefix_count(ctx["events"], "judge.")), "judge events", "info", refs=_lineage(events=_events_with_prefix(ctx["events"], ("judge.",)), graph_node_ids=["judge"])),
        _stage("done_rework", "Done/Rework", str(verify["rework"]), "rework pressure", "warn" if verify["rework"] else "ok", refs=_lineage(events=rework_events, graph_node_ids=["done_rework"])),
    ]
    return metrics, stages, _graph("ring", stages)


def _event_payload(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    dispatch = ctx["dispatch"]
    notifications = dispatch.get("notifications", []) or []
    task_events = _events_with_prefix(ctx["events"], ("task.",))
    orchestrator_events = _events_with_prefix(ctx["events"], ("orchestrator.",))
    decision_events = _events_with_type(ctx["events"], ("orchestrator.decision.recorded",))
    dispatch_events = _events_with_type(ctx["events"], ("task.dispatched",))
    worker_events = _events_with_prefix(ctx["events"], ("worker.",))
    metrics = [
        _metric("ready", "Ready", dispatch.get("ready_task_count", 0), dispatch.get("ready_task_count", 0), "warn", "ready task count", refs=_lineage(graph_node_ids=["ingest", "decision"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("dispatchable", "Dispatchable", dispatch.get("dispatchable_worker_count", 0), dispatch.get("dispatchable_worker_count", 0), "info", "dispatchable workers", refs=_lineage(graph_node_ids=["decision", "dispatch"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("why_not", "Why-not", len(notifications), len(notifications), "warn" if notifications else "ok", "dispatch diagnostics", refs=_lineage(graph_node_ids=["decision"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _metric("events", "Events", len(ctx["events"]), len(ctx["events"]), "info", "scoped event count", refs=_lineage(events=ctx["events"], graph_node_ids=["ingest"])),
    ]
    stages = [
        _stage("ingest", "Event Ingest", str(len(ctx["events"])), "events", "info", refs=_lineage(events=ctx["events"], graph_node_ids=["ingest"])),
        _stage("reactor", "Reactor", str(_prefix_count(ctx["events"], "orchestrator.")), "orchestrator events", "info", refs=_lineage(events=orchestrator_events, graph_node_ids=["reactor"])),
        _stage("decision", "Decision", str(_event_count(ctx["events"], "orchestrator.decision.recorded")), "decisions", "info", refs=_lineage(events=decision_events, graph_node_ids=["decision"], source_projection_refs=["dispatch-diagnostics.v1"])),
        _stage("dispatch", "Dispatch", str(_event_count(ctx["events"], "task.dispatched")), "task dispatched", "info", refs=_lineage(events=dispatch_events or task_events, graph_node_ids=["dispatch"])),
        _stage("worker_ack", "Worker Ack", str(_prefix_count(ctx["events"], "worker.")), "worker events", "info", refs=_lineage(events=worker_events, graph_node_ids=["worker_ack"])),
    ]
    return metrics, stages, _graph("dag", stages)


def _hill_payload(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    loop_summary = ctx["loop_projection"].get("summary") or {}
    hill_events = [event for _seq, event in ctx["events"] if event.type.startswith(_HILL_PREFIXES)]
    loop_ids = _loop_ids(ctx)
    behavior_loop_ids = _ids_from_rows(ctx["loop_projection"].get("behaviors") or [], "loop_id")
    diagnosis_loop_ids = _ids_from_rows(ctx["loop_projection"].get("diagnoses") or [], "loop_id")
    candidate_loop_ids = _ids_from_rows(ctx["loop_projection"].get("candidates") or [], "loop_id")
    recovered_loop_ids = [
        str(loop.get("loop_id") or "")
        for loop in ctx["loop_projection"].get("loops") or []
        if str(loop.get("status") or "") == "recovered"
    ]
    metrics = [
        _metric("loops", "Feedback Loops", loop_summary.get("total", 0), loop_summary.get("total", 0), "info", "loop.v1 total", refs=_lineage(events=ctx["events"], loop_ids=loop_ids, graph_node_ids=["failure_trace"], source_projection_refs=["loop.v1"])),
        _metric("open", "Open", loop_summary.get("open", 0), loop_summary.get("open", 0), "warn", "open improvement loops", refs=_lineage(loop_ids=[str(loop.get("loop_id") or "") for loop in ctx["loop_projection"].get("loops") or [] if str(loop.get("status") or "") == "open"], graph_node_ids=["failure_trace"], source_projection_refs=["loop.v1"])),
        _metric("candidates", "Candidates", loop_summary.get("candidate_count", 0), loop_summary.get("candidate_count", 0), "info", "improvement candidates", refs=_lineage(loop_ids=candidate_loop_ids, graph_node_ids=["proposal"], source_projection_refs=["loop.v1"])),
        _metric("hill_events", "Improve Events", len(hill_events), len(hill_events), "info", "replan/autoresearch/action events", refs=_lineage(events=[item for item in ctx["events"] if item[1].type.startswith(_HILL_PREFIXES)], graph_node_ids=["proposal", "improved"], source_projection_refs=["loop.v1"])),
    ]
    stages = [
        _stage("failure_trace", "Failure Trace", str(loop_summary.get("total", 0)), "projected loops", "info", refs=_lineage(loop_ids=loop_ids, graph_node_ids=["failure_trace"], source_projection_refs=["loop.v1"])),
        _stage("pattern", "Pattern", str(loop_summary.get("behavior_count", 0)), "behaviors", "warn" if loop_summary.get("behavior_count", 0) else "muted", refs=_lineage(loop_ids=behavior_loop_ids, graph_node_ids=["pattern"], source_projection_refs=["loop.v1"])),
        _stage("diagnosis", "Diagnosis", str(len(ctx["loop_projection"].get("diagnoses") or [])), "diagnoses", "info", refs=_lineage(loop_ids=diagnosis_loop_ids, graph_node_ids=["diagnosis"], source_projection_refs=["loop.v1"])),
        _stage("proposal", "Proposal", str(loop_summary.get("candidate_count", 0)), "candidates", "info", refs=_lineage(loop_ids=candidate_loop_ids, graph_node_ids=["proposal"], source_projection_refs=["loop.v1"])),
        _stage("improved", "Verified Improvement", str(loop_summary.get("recovered", 0)), "recovered", "ok", refs=_lineage(loop_ids=recovered_loop_ids, graph_node_ids=["improved"], source_projection_refs=["loop.v1"])),
    ]
    return metrics, stages, _graph("ring", stages)


def _graph(layout: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
    ref_keys = ("source_event_ids", "task_ids", "trace_ids", "loop_ids", "graph_node_ids", "source_projection_refs")
    nodes = [
        {
            "id": stage["id"],
            "kind": "stage",
            "label": stage["label"],
            "status": stage["tone"],
            "value": stage["value"],
            **{key: stage[key] for key in ref_keys if stage.get(key)},
        }
        for stage in stages
    ]
    edges = [
        {"from": left["id"], "to": right["id"], "kind": "next", "status": "projection"}
        for left, right in zip(stages, stages[1:])
    ]
    if layout == "ring" and len(stages) > 2:
        edges.append({"from": stages[-1]["id"], "to": stages[0]["id"], "kind": "cycle", "status": "projection"})
    return {"layout_hint": layout, "nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}


def _metric(metric_id: str, label: str, value: object, raw: object, tone: str, detail: str, *, refs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": metric_id, "label": label, "value": str(value), "raw": raw, "tone": tone, "detail": detail, **(refs or {})}


def _stage(stage_id: str, label: str, value: str, detail: str, tone: str, *, refs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": stage_id, "label": label, "value": value, "detail": detail, "tone": tone, **(refs or {})}


def _percent(value: int, total: int) -> int:
    return 0 if total <= 0 else max(0, min(100, round((value / total) * 100)))


def _prefix_count(events: EventSlice, prefix: str) -> int:
    return sum(1 for _seq, event in events if event.type.startswith(prefix))


def _event_count(events: EventSlice, event_type: str) -> int:
    return sum(1 for _seq, event in events if event.type == event_type)


def _events_with_prefix(events: EventSlice, prefixes: tuple[str, ...]) -> list[tuple[int, ZfEvent]]:
    return [item for item in events if item[1].type.startswith(prefixes)]


def _events_with_type(events: EventSlice, types: tuple[str, ...]) -> list[tuple[int, ZfEvent]]:
    allowed = set(types)
    return [item for item in events if item[1].type in allowed]


def _tasks_with_state(tasks: list[Task], ready_ids: set[str], statuses: set[str]) -> list[Task]:
    include_ready_ids = statuses == _READY
    return [
        task for task in tasks
        if task.status in statuses or (include_ready_ids and task.id in ready_ids)
    ]


def _lineage(
    *,
    events: EventSlice | None = None,
    tasks: Iterable[Task] | None = None,
    task_ids: Iterable[str] | None = None,
    trace_ids: Iterable[str] | None = None,
    loop_ids: Iterable[str] | None = None,
    graph_node_ids: Iterable[str] | None = None,
    source_projection_refs: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    event_items = list(events or [])
    _put(out, "source_event_ids", [event.id for _seq, event in event_items])
    _put(out, "task_ids", [
        *(task.id for task in tasks or []),
        *(task_ids or []),
        *(str(event.task_id or payload(event).get("task_id") or "") for _seq, event in event_items),
    ])
    _put(out, "trace_ids", [
        *(trace_ids or []),
        *(str(event.correlation_id or payload(event).get("trace_id") or "") for _seq, event in event_items),
    ])
    _put(out, "loop_ids", loop_ids or [])
    _put(out, "graph_node_ids", graph_node_ids or [])
    _put(out, "source_projection_refs", source_projection_refs or [])
    return out


def _put(out: dict[str, list[str]], key: str, values: Iterable[object]) -> None:
    refs = _dedupe_limited(str(value) for value in values if str(value or ""))
    if refs:
        out[key] = refs


def _dedupe_limited(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= _REF_LIMIT:
            break
    return out


def _loop_ids(ctx: dict[str, Any]) -> list[str]:
    return _ids_from_rows(ctx["loop_projection"].get("loops") or [], "loop_id")


def _ids_from_rows(rows: Iterable[dict[str, Any]], key: str) -> list[str]:
    return _dedupe_limited(str(row.get(key) or "") for row in rows)


def _feed(events: EventSlice, lens: str) -> list[dict[str, Any]]:
    selected = events[-40:]
    if lens == "verification":
        selected = [item for item in events if item[1].type.startswith(_VERIFY_PREFIXES)][-40:]
    elif lens == "event_driven":
        selected = [item for item in events if item[1].type.startswith(("task.", "orchestrator.", "worker.", "fanout."))][-40:]
    elif lens == "hill_climbing":
        selected = [item for item in events if item[1].type.startswith(_HILL_PREFIXES) or item[1].type.startswith("task.rework.")][-40:]
    return [
        {
            "seq": seq,
            "event_id": event.id,
            "event_type": event.type,
            "task_id": event.task_id,
            "status": event_status(event),
            "ts": event.ts,
            "trace_id": str(event.correlation_id or payload(event).get("trace_id") or ""),
        }
        for seq, event in reversed(selected)
    ]


def _diagnostics(ctx: dict[str, Any]) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if not ctx["tasks"]:
        diagnostics.append({"kind": "no_tasks", "message": "no task projection rows matched the current scope"})
    if not ctx["events"]:
        diagnostics.append({"kind": "no_events", "message": "no event projection rows matched the current scope"})
    return diagnostics
