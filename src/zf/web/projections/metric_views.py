"""Web entrypoints over incremental facts and bounded semantic event slices."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from zf.web.projections import metric_facts as facts, metric_tasks as tasks, metric_costs as costs


def refresh(state_dir: Path, config=None) -> None:
    facts.refresh_events(state_dir, config)
    tasks.refresh(state_dir)
    costs.refresh(state_dir)


def core(state_dir: Path, config=None) -> dict:
    from zf.web.projections.metric_reducer import snapshot
    try:
        refresh(state_dir, config)
        stats, traces = tasks.core_inputs(state_dir)
    except sqlite3.DatabaseError:
        from zf.core.metrics.collector import MetricsCollector
        from zf.core.cost.tracker import CostTracker
        from zf.core.task.store import TaskStore
        from zf.core.events.factory import event_log_from_project
        return MetricsCollector.compute(events=event_log_from_project(state_dir, config=config),
            tasks=TaskStore(state_dir / "kanban.json"), cost=CostTracker(state_dir / "cost.jsonl")).to_dict()
    return snapshot(facts.load_meta(state_dir, "core"), stats, traces,
                    costs.ProjectedCostTracker(state_dir / "cost.jsonl"))


def oa_events(state_dir: Path, config=None):
    facts.refresh_events(state_dir, config)
    # Includes every event family inspected by the canonical OA reducer;
    # payloads stay in the ledger and only these refs are hydrated.
    return [event for _, event in facts.events(state_dir, config=config, full=True,
        prefixes=("orchestrator.semantic.", "workflow.operation.", "owner.delivery.narrative."),
        types=("workflow.call.result.admitted", "agent.usage", "owner.visible_message.requested",
               "dev.build.done", "fanout.child.completed", "lane.stage.completed", "verify.passed", "task.rework.requested"))]


def fleet(state_dir: Path, config=None, *, now=None) -> dict:
    from zf.runtime.fleet_efficiency import build_task_flow_stats, build_role_efficiency
    facts.refresh_events(state_dir, config)
    tasks.refresh(state_dir)
    selected = facts.events(state_dir, types=("task.dispatched", "task.done.accepted", "task.status_changed",
                            "task.rework.requested", "worker.respawned", "worker.respawn.completed"))
    current = {task.id: task for task in tasks.rows(state_dir)}
    clock = now or datetime.now(timezone.utc)
    return {"task_flow": build_task_flow_stats(selected, current, now=clock),
            "role_efficiency": build_role_efficiency(selected, current, now=clock)}


def pulse(state_dir: Path, config=None, *, now=None) -> dict:
    from zf.web import overview_pulse as p
    facts.refresh_events(state_dir, config)
    tasks.refresh(state_dir)
    current = now or datetime.now(timezone.utc)
    # Day selection retains EventLog archive-day semantics, including future
    # timestamps and late records in the active segment.
    selected = [event for _, event in facts.events(state_dir, days=2)]
    loop = p.build_runtime_loop_health(state_dir, events=selected, now=current)
    return {"schema_version": p.SCHEMA_VERSION, "is_derived_projection": True,
            "generated_at": current.isoformat(),
            "run_pulse": {**p._run_pulse(selected, current), "loop": {"status": loop.get("status"), "age_seconds": loop.get("age_seconds")},
                          "sessions": p._sessions(state_dir, config, selected)},
            "task_flow": p._task_flow(state_dir, config, selected, current,
                                      projected_tasks=tasks.rows(state_dir), projected_counts=tasks.counts(state_dir)),
            "attention": p._attention(selected, current), "why_not": p._why_not(state_dir, selected, loop)}
