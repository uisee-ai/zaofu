"""静止审计器:三向对账 + 断点定位 + 死窗重发(07-17 立项)。

七类"tmux 全无活动"的机器化检测。判定原则:run 必须能回答
"为什么现在没有事情发生";答不上来 = run.stalled。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.stillness_auditor import (
    RUN_STALLED_EVENT,
    StillnessState,
    audit_stillness,
    emit_stalled_and_redrive,
)

_NOW = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)


def _ev(etype: str, *, minutes_ago: float, eid: str = "", task: str = "",
        **payload) -> ZfEvent:
    kw = {"id": eid} if eid else {}
    return ZfEvent(
        type=etype, actor="zf-cli",
        ts=(_NOW - timedelta(minutes=minutes_ago)).isoformat(),
        task_id=task or None, payload=payload, **kw,
    )


def test_inflight_fanout_is_active() -> None:
    events = [
        _ev("fanout.started", minutes_ago=10, fanout_id="f1",
            trigger_event_id="evt-t"),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert r.state == "active"


def test_unconsumed_driver_after_boot_is_derivation_gap_stall() -> None:
    events = [
        _ev("loop.started", minutes_ago=30),
        _ev("task_map.ready", minutes_ago=20, eid="evt-tm"),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert r.state == "stalled"
    assert r.breakpoints[0]["breakpoint"] == "derivation_gap"
    assert r.breakpoints[0]["event_id"] == "evt-tm"


def test_event_older_than_boot_is_dead_window() -> None:
    events = [
        _ev("flow.discovery.completed", minutes_ago=20, eid="evt-dc"),
        _ev("loop.started", minutes_ago=10),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert r.state == "stalled"
    assert r.breakpoints[0]["breakpoint"] == "dead_window"


def test_consumed_trigger_is_not_pending() -> None:
    events = [
        _ev("task_map.ready", minutes_ago=20, eid="evt-tm"),
        _ev("fanout.started", minutes_ago=19, fanout_id="f1",
            trigger_event_id="evt-tm"),
        _ev("fanout.aggregate.completed", minutes_ago=18, fanout_id="f1"),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert r.state == "active"
    assert not r.breakpoints


def test_quarantined_trigger_counts_as_consumed() -> None:
    events = [
        _ev("task_map.ready", minutes_ago=20, eid="evt-tm"),
        _ev("candidate.rework.quarantined", minutes_ago=19,
            trigger_event_id="evt-tm"),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert not r.breakpoints


def test_open_escalation_parks_not_stalls() -> None:
    events = [
        _ev("loop.started", minutes_ago=30),
        _ev("task_map.ready", minutes_ago=20, eid="evt-tm"),
        _ev("human.escalate", minutes_ago=15),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert r.state == "parked"
    assert r.reason == "open_escalation"
    # 决议后恢复 stalled 判定
    events.append(_ev("human.escalation.acknowledged", minutes_ago=5))
    r2 = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert r2.state == "stalled"


def test_recent_budget_exceeded_parks() -> None:
    events = [
        _ev("loop.started", minutes_ago=30),
        _ev("task_map.ready", minutes_ago=20, eid="evt-tm"),
        _ev("cost.budget.exceeded", minutes_ago=2),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert r.state == "parked"
    assert r.reason == "budget_exceeded"


def test_grace_window_suppresses_fresh_events() -> None:
    events = [
        _ev("loop.started", minutes_ago=30),
        _ev("task_map.ready", minutes_ago=1, eid="evt-tm"),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert not r.breakpoints


def test_final_lane_completion_without_next_slot_is_not_pending() -> None:
    events = [
        _ev("loop.started", minutes_ago=30),
        _ev("lane.stage.completed", minutes_ago=20, eid="evt-l",
            next_stage_slot=""),
    ]
    r = audit_stillness(events, now_epoch=_NOW.timestamp())
    assert not r.breakpoints


def test_emit_is_idempotent_and_redrives_dead_window_once(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    log.append(_ev("flow.discovery.completed", minutes_ago=20, eid="evt-dc",
                   candidate_ref="candidate/default"))
    log.append(_ev("loop.started", minutes_ago=10))

    r = audit_stillness(log.read_all(), now_epoch=_NOW.timestamp())
    assert r.state == "stalled"
    emitted = emit_stalled_and_redrive(writer, log.read_all(), r)
    assert emitted == {"stalled": 1, "redriven": 1}

    types = [e.type for e in log.read_all()]
    assert RUN_STALLED_EVENT in types
    redrives = [
        e for e in log.read_all()
        if e.type == "flow.discovery.completed"
        and (e.payload or {}).get("redrive_of") == "evt-dc"
    ]
    assert len(redrives) == 1
    assert redrives[0].payload.get("rework_of") == "evt-dc"  # 代际语义

    # 第二轮审计:redrive 已存在 → 原事件不再算 pending;重复 emit 幂等
    r2 = audit_stillness(log.read_all(), now_epoch=_NOW.timestamp())
    assert all(bp["event_id"] != "evt-dc" for bp in r2.breakpoints)
    emitted2 = emit_stalled_and_redrive(writer, log.read_all(), r)
    assert emitted2["stalled"] == 0  # 同 digest 不重复落账


def test_derivation_gap_is_reported_not_redriven(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    log.append(_ev("loop.started", minutes_ago=30))
    log.append(_ev("task_map.ready", minutes_ago=20, eid="evt-tm"))
    r = audit_stillness(log.read_all(), now_epoch=_NOW.timestamp())
    emitted = emit_stalled_and_redrive(writer, log.read_all(), r)
    assert emitted == {"stalled": 1, "redriven": 0}


def test_digest_counter_tracks_unchanged_pending() -> None:
    state = StillnessState()
    events = [
        _ev("loop.started", minutes_ago=30),
        _ev("task_map.ready", minutes_ago=20, eid="evt-tm"),
    ]
    for _ in range(3):
        audit_stillness(events, now_epoch=_NOW.timestamp(), state=state)
    assert state.unchanged_count == 2  # 首轮建基线,后两轮未变
