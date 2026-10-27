"""Sufficient statistics for the existing MetricsCollector formulas."""
from __future__ import annotations

from datetime import datetime
from zf.core.metrics.collector import MetricsSnapshot, _FAILURE_TYPES, STUCK_PROGRESS_TYPES


def empty_state() -> dict:
    return {"count": 0, "types": {}, "first": "", "last": "", "first_crash": None,
            "drift": 0, "stucks": 0, "first_stuck": 0, "last_stuck": 0,
            "pending_stucks": [], "recovered": 0}


def consume(state: dict, event, task: dict | None) -> None:
    index = state["count"]
    state["count"] += 1
    kind = event.type
    counts = state["types"]
    counts[kind] = counts.get(kind, 0) + 1
    if index == 0: state["first"] = event.ts
    state["last"] = event.ts
    payload = event.payload if isinstance(event.payload, dict) else {}
    if kind == "worker.drift.detected" and payload.get("signal") in ("thrashing", "repeat_decisions"):
        state["drift"] += 1
    if state["first_crash"] is None and kind in {"task.orphaned", "pane.crash", "worker.respawn.failed"}:
        state["first_crash"] = event.ts
    pending = [i for i in state["pending_stucks"] if index - i <= 7]
    if kind in STUCK_PROGRESS_TYPES:
        state["recovered"] += len(pending)
        pending = []
    if kind == "worker.stuck":
        if state["stucks"] == 0: state["first_stuck"] = index
        state["stucks"] += 1
        state["last_stuck"] = index
        pending.append(index)
    state["pending_stucks"] = pending
    if task is not None:
        task.setdefault("first", event.ts)
        task["last"] = event.ts
        task["count"] = task.get("count", 0) + 1
        task["created"] = task.get("created", False) or kind == "task.created"
        task["final"] = task.get("final", False) or kind in {"judge.passed", "verify.passed", "task.status_changed"}


def hours(first: str, last: str, divisor: float) -> float:
    return (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds() / divisor


def snapshot(state: dict, task_stats: dict, traces: list[dict], cost) -> dict:
    """Keep all 25 fields, including legacy windows and placeholder values."""
    snap = MetricsSnapshot(events_considered=state["count"])
    if state["count"]:
        try: snap.window_hours = max(hours(state["first"], state["last"], 3600), 1 / 3600)
        except (ValueError, TypeError): snap.window_hours = 1 / 3600
    counts = state["types"]
    done = task_stats["done"]
    snap.tasks_done = done
    if snap.window_hours:
        snap.throughput_per_hour = done / snap.window_hours
        snap.recycle_freq_per_hour = counts.get("worker.recycled", 0) / snap.window_hours
    if done: snap.rework_ratio = sum(counts.get(k, 0) for k in _FAILURE_TYPES) / done
    snap.vcr = task_stats["verified"] / task_stats["attempted"] if task_stats["attempted"] else 0.0
    dispatched = counts.get("task.dispatched", 0)
    if dispatched:
        snap.scope_violation_rate = counts.get("scope.violation", 0) / dispatched
        snap.budget_breach_rate = counts.get("cost.budget.exceeded", 0) / dispatched
        snap.goal_drift = state["drift"] / dispatched
    passed, failed = counts.get("discriminator.passed", 0), counts.get("discriminator.failed", 0)
    if passed + failed: snap.discriminator_catch_rate = failed / (passed + failed)
    stucks = state["stucks"]
    if stucks > 1: snap.mtts = (state["last_stuck"] - state["first_stuck"]) / (stucks - 1)
    elif stucks: snap.mtts = float(state["count"] - state["first_stuck"])
    if stucks: snap.stuck_recovery_rate = state["recovered"] / stucks
    setattr(snap, "_stuck_count", stucks)
    if state["first_crash"] is None: snap.crash_free_hours = snap.window_hours
    else:
        try: snap.crash_free_hours = max(hours(state["first"], state["first_crash"], 3600), 0.0)
        except (ValueError, TypeError): pass
    snap.resume_fidelity = 0.0 if counts.get("task.orphaned", 0) else 1.0
    if task_stats["trace_tasks"]:
        snap.avg_events_per_task = task_stats["trace_events"] / task_stats["trace_tasks"]
    if done:
        complete, minutes = 0, 0.0
        for row in traces:
            complete += int(row["created"] and row["final"])
            try: minutes += hours(row["first"], row["last"], 60)
            except (ValueError, TypeError): pass
        snap.trace_complete_rate = complete / done
        snap.avg_task_duration_minutes = minutes / done
    roles = cost.per_role_totals()
    if done:
        snap.cost_per_task = sum(s.total_usd for s in roles.values()) / done
        snap.token_per_task = sum(s.input_tokens + s.output_tokens for s in roles.values()) / done
    backends = cost.summary_by_backend()
    snap.cost_by_backend = {k: s.total_usd for k, s in backends.items()}
    snap.tokens_by_backend = {k: s.input_tokens + s.output_tokens for k, s in backends.items()}
    snap.alerts = snap.flag_alerts()
    return snap.to_dict()
