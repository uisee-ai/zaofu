"""Task-flow stats and per-role efficiency rollups (Overview/Agents metrics)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.fleet_efficiency import (
    build_role_efficiency,
    build_task_flow_stats,
)

NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _iso(delta_hours: float) -> str:
    return (NOW - timedelta(hours=delta_hours)).isoformat()


def _done_event(task_id: str, hours_ago: float, eid: str = "") -> tuple[int, ZfEvent]:
    return (1, ZfEvent(type="task.done.accepted", id=eid or f"e-{task_id}",
                       task_id=task_id, ts=_iso(hours_ago)))


def _task(task_id: str, *, status: str = "done", role: str = "dev",
          assigned: str = "", dispatched_hours_ago: float | None = None) -> Task:
    return Task(
        id=task_id, title=task_id, status=status,
        assigned_to=assigned or f"{role}-1",
        dispatched_at=_iso(dispatched_hours_ago) if dispatched_hours_ago is not None else None,
        contract=TaskContract(feature_id="F-1", owner_role=role),
    )


# --- task flow stats ---

def test_flow_stats_empty():
    out = build_task_flow_stats([], {}, now=NOW)
    assert out["done_24h"] == 0
    assert out["done_7d"] == [0] * 7
    assert out["oldest_in_progress_seconds"] is None


def test_flow_stats_24h_window_and_7d_buckets():
    events = [
        _done_event("T1", 1),       # today
        _done_event("T2", 30),      # yesterday (>24h)
        _done_event("T3", 6 * 24),  # 6 days ago
        _done_event("T4", 8 * 24),  # outside 7d
    ]
    out = build_task_flow_stats(events, {}, now=NOW)
    assert out["done_24h"] == 1
    assert out["done_7d"][6] == 1   # today bucket
    assert out["done_7d"][5] == 1   # yesterday
    assert out["done_7d"][0] == 1   # 6 days ago
    assert sum(out["done_7d"]) == 3
    assert out["throughput_per_hour_24h"] == round(1 / 24.0, 3)


def test_flow_stats_status_changed_requires_done_payload():
    events = [
        (1, ZfEvent(type="task.status_changed", id="e1", task_id="T1",
                    ts=_iso(1), payload={"status": "done"})),
        (2, ZfEvent(type="task.status_changed", id="e2", task_id="T2",
                    ts=_iso(1), payload={"status": "blocked"})),
    ]
    out = build_task_flow_stats(events, {}, now=NOW)
    assert out["done_24h"] == 1


def test_flow_stats_oldest_ages():
    tasks = {
        "T1": _task("T1", status="in_progress", dispatched_hours_ago=2),
        "T2": _task("T2", status="in_progress", dispatched_hours_ago=5),
        "T3": _task("T3", status="blocked", dispatched_hours_ago=1),
    }
    out = build_task_flow_stats([], tasks, now=NOW)
    assert out["oldest_in_progress_seconds"] == 5 * 3600
    assert out["oldest_blocked_seconds"] == 1 * 3600


# --- role efficiency ---

def test_role_efficiency_done_and_duration():
    tasks = {"T1": _task("T1"), "T2": _task("T2")}
    events = [
        (1, ZfEvent(type="task.dispatched", id="d1", task_id="T1", ts=_iso(3))),
        _done_event("T1", 2),  # 1h duration
        (3, ZfEvent(type="task.dispatched", id="d2", task_id="T2", ts=_iso(4))),
        _done_event("T2", 1),  # 3h duration
    ]
    rows = build_role_efficiency(events, tasks, now=NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "dev"
    assert row["done"] == 2
    assert row["avg_duration_minutes"] == 120.0  # (60 + 180) / 2


def test_role_efficiency_rework_and_respawn_attribution():
    tasks = {"T1": _task("T1", role="test", assigned="test-2")}
    events = [
        (1, ZfEvent(type="task.rework.requested", id="r1", task_id="T1", ts=_iso(2))),
        (2, ZfEvent(type="worker.respawned", id="w1", actor="judge-2", ts=_iso(2))),
        (3, ZfEvent(type="worker.respawn.completed", id="w2", ts=_iso(2),
                    payload={"role": "judge"})),
    ]
    rows = build_role_efficiency(events, tasks, now=NOW)
    by_role = {row["role"]: row for row in rows}
    assert by_role["test"]["rework"] == 1
    assert by_role["judge"]["respawn"] == 2


def test_role_efficiency_window_excludes_old_events():
    tasks = {"T1": _task("T1")}
    events = [_done_event("T1", 9 * 24)]  # outside 7d
    assert build_role_efficiency(events, tasks, now=NOW) == []


def test_role_efficiency_instance_suffix_stripped():
    tasks = {"T1": Task(id="T1", title="t", status="done", assigned_to="dev-3")}
    rows = build_role_efficiency([_done_event("T1", 1)], tasks, now=NOW)
    assert rows[0]["role"] == "dev"
