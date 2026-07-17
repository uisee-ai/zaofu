"""恢复案卷:三表对账矛盾检测(07-17 UISSE 实弹立项)。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.recovery_case_file import build_case_file

_NOW = datetime(2026, 7, 17, 9, 0, 0, tzinfo=timezone.utc)


def _ev(etype: str, *, minutes_ago: float, task: str = "", **payload) -> ZfEvent:
    return ZfEvent(
        type=etype, actor="zf-cli",
        ts=(_NOW - timedelta(minutes=minutes_ago)).isoformat(),
        task_id=task or None, payload=payload,
    )


def test_wip_without_carrier_detected() -> None:
    """UISSE 实弹场景:child failed + kanban in_progress + worker idle。"""
    events = [
        _ev("fanout.child.dispatched", minutes_ago=60, task="T1",
            fanout_id="f1", child_id="dev-lane-0-T1"),
        _ev("fanout.child.failed", minutes_ago=40, task="T1",
            fanout_id="f1", child_id="dev-lane-0-T1"),
    ]
    cf = build_case_file(
        events,
        tasks=[{"id": "T1", "status": "in_progress", "assigned_to": "dev-lane-0"}],
        instance_states={"dev-lane-0": "idle"},
        now_epoch=_NOW.timestamp(),
    )
    kinds = [c["kind"] for c in cf["contradictions"]]
    assert "wip_without_carrier" in kinds


def test_busy_worker_is_not_contradiction() -> None:
    events = [
        _ev("fanout.child.dispatched", minutes_ago=60, task="T1",
            fanout_id="f1", child_id="c1"),
        _ev("fanout.child.failed", minutes_ago=40, task="T1",
            fanout_id="f1", child_id="c1"),
    ]
    cf = build_case_file(
        events,
        tasks=[{"id": "T1", "status": "in_progress", "assigned_to": "dev-lane-0"}],
        instance_states={"dev-lane-0": "busy"},  # 正在返工中,不算矛盾
        now_epoch=_NOW.timestamp(),
    )
    assert cf["contradictions"] == []


def test_inflight_child_is_not_contradiction() -> None:
    events = [
        _ev("fanout.child.dispatched", minutes_ago=5, task="T1",
            fanout_id="f1", child_id="c1"),
    ]
    cf = build_case_file(
        events,
        tasks=[{"id": "T1", "status": "in_progress", "assigned_to": "dev-lane-0"}],
        instance_states={"dev-lane-0": "busy"},
        now_epoch=_NOW.timestamp(),
    )
    assert cf["contradictions"] == []


def test_queue_stuck_detected() -> None:
    """UISSE 实弹:队头死,6 个排队 child 无在飞、fanout 未终局。"""
    events = [
        _ev("fanout.child.queued", minutes_ago=40, fanout_id="f1",
            child_id="queued-T2-2"),
        _ev("fanout.child.queued", minutes_ago=40, fanout_id="f1",
            child_id="queued-T3-3"),
    ]
    cf = build_case_file(
        events, tasks=[], instance_states={}, now_epoch=_NOW.timestamp(),
    )
    qs = [c for c in cf["contradictions"] if c["kind"] == "queue_stuck"]
    assert qs and qs[0]["fanout"] == "f1"
    assert qs[0]["evidence"]["queued"] == ["queued-T2-2", "queued-T3-3"]


def test_queue_with_inflight_or_terminal_is_fine() -> None:
    events = [
        _ev("fanout.child.queued", minutes_ago=40, fanout_id="f1", child_id="q1"),
        _ev("fanout.child.dispatched", minutes_ago=30, fanout_id="f1", child_id="d1"),
        _ev("fanout.child.queued", minutes_ago=40, fanout_id="f2", child_id="q2"),
        _ev("fanout.cancelled", minutes_ago=30, fanout_id="f2"),
    ]
    cf = build_case_file(
        events, tasks=[], instance_states={}, now_epoch=_NOW.timestamp(),
    )
    assert all(c["kind"] != "queue_stuck" for c in cf["contradictions"])


def test_ready_but_starved_detected() -> None:
    events = [
        _ev("task.requeued", minutes_ago=20, task="T1", task_id="T1"),
    ]
    cf = build_case_file(
        events,
        tasks=[{"id": "T1", "status": "backlog", "assigned_to": "dev-lane-0"}],
        instance_states={"dev-lane-0": "idle"},
        now_epoch=_NOW.timestamp(),
    )
    assert any(c["kind"] == "ready_but_starved" for c in cf["contradictions"])


def test_current_state_budget_stale_and_escalations() -> None:
    from types import SimpleNamespace

    events = [
        _ev("cost.budget.exceeded", minutes_ago=30, budget_usd=150.0,
            current_usd=151.0),
        _ev("human.escalate", minutes_ago=20, decision_token="hdec-1",
            reason="a"),
        _ev("human.escalate", minutes_ago=10, reason="tokenless"),
        _ev("human.escalation.acknowledged", minutes_ago=5,
            decision_token="hdec-1"),
    ]
    cf = build_case_file(
        events, tasks=[], instance_states={},
        now_epoch=_NOW.timestamp(),
        config=SimpleNamespace(global_budget_usd=350.0),
    )
    cs = cf["current_state"]
    assert cs["budget_stale"] is True  # 已提额,旧超限事件过时
    assert len(cs["open_escalations"]) == 1  # tokenless 仍开(且被标 token=None)
    assert cs["open_escalations"][0]["token"] is None
