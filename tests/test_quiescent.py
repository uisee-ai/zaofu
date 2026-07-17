"""U3/G3:escalate 后静默 + 唤醒即恢复(灰度,默认关)。

r6.1 实弹:终局 escalate 后 4h probe/drift/tick 空烧 6.4M;自愈路径
(escalate 后 1 分钟内 replan 出进展)必须不受影响 → 宽限窗。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.quiescent import (
    QUIESCENT_ENTERED_EVENT,
    QUIESCENT_EXITED_EVENT,
    mark_quiescent_transition,
    quiescent_now,
)

_NOW = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)


def _cfg(enabled: bool = True, quiescent: bool = True):
    return SimpleNamespace(goal=SimpleNamespace(
        enabled=enabled, quiescent_after_escalate=quiescent,
    ))


def _ev(etype: str, *, minutes_ago: float, eid: str = "") -> ZfEvent:
    ts = (_NOW - timedelta(minutes=minutes_ago)).isoformat()
    kw = {"id": eid} if eid else {}
    return ZfEvent(type=etype, actor="zf-cli", ts=ts, payload={}, **kw)


def test_unresolved_escalate_past_grace_is_quiescent() -> None:
    events = [_ev("human.escalate", minutes_ago=30, eid="esc-1")]
    s = quiescent_now(events, config=_cfg(), now_epoch=_NOW.timestamp())
    assert s.quiescent is True
    assert s.escalate_event_id == "esc-1"


def test_grace_window_lets_self_heal_run() -> None:
    # r6.1 续跑自愈:escalate 后 1 分钟内 replan——宽限窗内不静默
    events = [_ev("human.escalate", minutes_ago=5)]
    s = quiescent_now(events, config=_cfg(), now_epoch=_NOW.timestamp())
    assert s.quiescent is False
    assert s.reason == "grace_window"


def test_progress_after_escalate_stays_active() -> None:
    events = [
        _ev("human.escalate", minutes_ago=30),
        _ev("task_map.ready", minutes_ago=29),
    ]
    s = quiescent_now(events, config=_cfg(), now_epoch=_NOW.timestamp())
    assert s.quiescent is False
    assert s.reason == "woken"


def test_operator_wake_event_stays_active() -> None:
    events = [
        _ev("human.escalate", minutes_ago=30),
        _ev("runtime.resume.requested", minutes_ago=10),
    ]
    s = quiescent_now(events, config=_cfg(), now_epoch=_NOW.timestamp())
    assert s.quiescent is False


def test_escalation_acknowledged_wakes_run() -> None:
    # 07-16 实弹:operator dismiss(human.escalation.acknowledged)后 run
    # 必须退出静默——决议本身就是操作员动作
    events = [
        _ev("human.escalate", minutes_ago=30),
        _ev("human.escalation.acknowledged", minutes_ago=10),
    ]
    s = quiescent_now(events, config=_cfg(), now_epoch=_NOW.timestamp())
    assert s.quiescent is False
    assert s.reason == "woken"


def test_new_escalate_resets_wake() -> None:
    events = [
        _ev("human.escalate", minutes_ago=60),
        _ev("task_map.ready", minutes_ago=59),
        _ev("human.escalate", minutes_ago=30, eid="esc-2"),
    ]
    s = quiescent_now(events, config=_cfg(), now_epoch=_NOW.timestamp())
    assert s.quiescent is True
    assert s.escalate_event_id == "esc-2"


def test_disabled_by_default_zero_regression() -> None:
    events = [_ev("human.escalate", minutes_ago=120)]
    assert quiescent_now(
        events, config=_cfg(enabled=False), now_epoch=_NOW.timestamp(),
    ).quiescent is False
    assert quiescent_now(
        events, config=_cfg(quiescent=False), now_epoch=_NOW.timestamp(),
    ).quiescent is False
    # goal 段缺失的旧配置对象
    assert quiescent_now(
        events, config=SimpleNamespace(), now_epoch=_NOW.timestamp(),
    ).quiescent is False


def test_transition_events_dedupe(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    from zf.runtime.quiescent import QuiescentStatus

    entered = QuiescentStatus(True, "escalate_unresolved", "esc-1")
    assert mark_quiescent_transition(writer, log.read_all(), status=entered) is True
    assert mark_quiescent_transition(writer, log.read_all(), status=entered) is False
    active = QuiescentStatus(False, "woken")
    assert mark_quiescent_transition(writer, log.read_all(), status=active) is True
    assert mark_quiescent_transition(writer, log.read_all(), status=active) is False
    types = [e.type for e in log.read_all()]
    assert types == [QUIESCENT_ENTERED_EVENT, QUIESCENT_EXITED_EVENT]


def test_tick_services_gate_returns_empty_when_quiescent(tmp_path: Path) -> None:
    from zf.runtime.tick_services import (
        TickServiceIntervals,
        TickServiceState,
        run_standard_tick_services,
    )

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(_ev("human.escalate", minutes_ago=30))
    orch = SimpleNamespace(
        event_log=log,
        event_writer=EventWriter(log),
        state_dir=state_dir,
        config=_cfg(),
        project_root=tmp_path,
    )
    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=0.0,
        intervals=TickServiceIntervals(),
    )
    assert result.heartbeat_sweep is False
    assert result.supervisor_inspection is False
    entered = [e for e in log.read_all() if e.type == QUIESCENT_ENTERED_EVENT]
    assert len(entered) == 1


def test_tick_services_gate_skips_during_shutdown_drain(tmp_path: Path) -> None:
    # ZF-STOP-TAIL-01:停机排空窗内探针/RM 立案全体跳过(07-16 实弹:
    # 拖尾 5 分钟里一秒 8 连发 autoresearch 立案)。
    from zf.runtime.tick_services import (
        TickServiceIntervals,
        TickServiceState,
        run_standard_tick_services,
    )

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "shutdown-requested").write_text("")
    log = EventLog(state_dir / "events.jsonl")
    orch = SimpleNamespace(
        event_log=log,
        event_writer=EventWriter(log),
        state_dir=state_dir,
        config=_cfg(enabled=False),
        project_root=tmp_path,
    )
    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=0.0,
        intervals=TickServiceIntervals(),
    )
    assert result.heartbeat_sweep is False
    assert result.supervisor_inspection is False
    assert log.read_all() == []  # 排空窗零立案零副作用
