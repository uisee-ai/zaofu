"""doc 80 rev1 § 7 P4 — watcher quiesce state machine.

R14 真跑:safe_halt 之后 watcher 不停 tick,整夜空转记 878 条
``orchestrator.dispatch_skipped``。fix:watcher 监听 ``runtime.safe_halted``
/ ``dispatch.paused`` 事件 → 进入 quiesce 态,polling 间隔放大;
``runtime.resumed`` / ``dispatch.resumed`` 解 quiesce。

Goal: 用 watcher 自己的 state 解决 R14-2 的空转噪音,不动 dispatch 暂停
语义,不引入新 sweep。tests 直接以 events 驱动 quiesce / resume,不依赖
真实 file polling — ``poll_once`` 是 watcher 的"消费一次 events"入口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.watcher import EventWatcher


def _watcher(tmp_path: Path, **kwargs) -> EventWatcher:
    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    on_event = kwargs.pop("on_event", lambda _line: None)
    return EventWatcher(events_path, on_event, **kwargs)


def _append(tmp_path: Path, event_type: str, payload: dict | None = None) -> None:
    EventWriter(EventLog(tmp_path / "events.jsonl")).emit(
        event_type, actor="test", payload=payload or {}
    )


def test_quiesced_defaults_to_false(tmp_path):
    w = _watcher(tmp_path)
    assert w.is_quiesced() is False


def test_default_quiesce_patterns_include_safe_halted_and_paused(tmp_path):
    w = _watcher(tmp_path)
    assert "runtime.safe_halted" in w.quiesce_patterns
    assert "dispatch.paused" in w.quiesce_patterns


def test_default_resume_patterns_include_resumed_events(tmp_path):
    w = _watcher(tmp_path)
    assert "runtime.resumed" in w.resume_patterns
    assert "dispatch.resumed" in w.resume_patterns


def test_safe_halted_event_sets_quiesced(tmp_path):
    w = _watcher(tmp_path)
    _append(tmp_path, "runtime.safe_halted", {"reason": "infra retry exhausted"})
    w.poll_once()
    assert w.is_quiesced() is True


def test_dispatch_paused_event_also_sets_quiesced(tmp_path):
    w = _watcher(tmp_path)
    _append(tmp_path, "dispatch.paused", {"reason": "manual operator pause"})
    w.poll_once()
    assert w.is_quiesced() is True


def test_resumed_event_clears_quiesced(tmp_path):
    w = _watcher(tmp_path)
    _append(tmp_path, "runtime.safe_halted", {})
    w.poll_once()
    assert w.is_quiesced() is True
    _append(tmp_path, "runtime.resumed", {"by": "operator zf resume"})
    w.poll_once()
    assert w.is_quiesced() is False


def test_dispatch_resumed_event_also_clears(tmp_path):
    w = _watcher(tmp_path)
    _append(tmp_path, "dispatch.paused", {})
    w.poll_once()
    assert w.is_quiesced() is True
    _append(tmp_path, "dispatch.resumed", {})
    w.poll_once()
    assert w.is_quiesced() is False


def test_events_still_consumed_during_quiesce(tmp_path):
    """quiesce 是 polling 间隔放大,不是停止 read events — 否则 watcher
    在 quiesce 后看不到 runtime.resumed,死锁。"""
    captured: list[str] = []
    w = _watcher(tmp_path, on_event=lambda line: captured.append(line))
    _append(tmp_path, "runtime.safe_halted", {})
    w.poll_once()
    assert w.is_quiesced() is True
    captured.clear()
    _append(tmp_path, "orchestrator.decision.recorded", {"x": 1})
    w.poll_once()
    assert len(captured) == 1, "events must continue to be consumed during quiesce"


def test_explicit_patterns_override_defaults(tmp_path):
    w = _watcher(
        tmp_path,
        quiesce_patterns=["custom.halt"],
        resume_patterns=["custom.go"],
    )
    assert "runtime.safe_halted" not in w.quiesce_patterns
    _append(tmp_path, "runtime.safe_halted", {})
    w.poll_once()
    assert w.is_quiesced() is False  # not in the explicit list
    _append(tmp_path, "custom.halt", {})
    w.poll_once()
    assert w.is_quiesced() is True
    _append(tmp_path, "custom.go", {})
    w.poll_once()
    assert w.is_quiesced() is False


def test_quiesce_factor_amplifies_poll_interval(tmp_path):
    """The polling interval used by `run` is ``base * factor`` while
    quiesced. Verify the calculation, not the real sleep — testing the real
    loop is timing-fragile."""
    w = _watcher(tmp_path, quiesce_factor=20.0)
    assert w.effective_poll_interval(base=0.5) == pytest.approx(0.5)
    _append(tmp_path, "runtime.safe_halted", {})
    w.poll_once()
    assert w.effective_poll_interval(base=0.5) == pytest.approx(10.0)


def test_default_quiesce_factor_is_ten(tmp_path):
    w = _watcher(tmp_path)
    _append(tmp_path, "runtime.safe_halted", {})
    w.poll_once()
    assert w.effective_poll_interval(base=0.5) == pytest.approx(5.0)  # 0.5 × 10
