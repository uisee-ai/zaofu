"""131-P2-4: RM 只对 attempt-ready failure 派 rework(avbs-r5 活锁回归)。"""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_resume import _rework_attempt_ready

_T = "AVBS-SCENE-001"


def _e(event_type: str, payload: dict | None = None) -> ZfEvent:
    return ZfEvent(type=event_type, actor="t", task_id=_T, payload=payload or {})


def test_completion_after_assignment_blocks_rework() -> None:
    """r5 实案:assigned → completed,silent_stall 是过期信号,不许再派。"""
    events = [
        _e("task.rework.requested"),
        _e("task.assigned"),
        _e("workflow.child.completed", {"dispatch_id": "disp-1", "state": "DONE"}),
    ]
    assert _rework_attempt_ready(events, _T) is False


def test_failure_after_completion_allows_rework() -> None:
    """合法流:completed 之后出现质量失败(review.rejected)→ rework 照常。"""
    events = [
        _e("task.assigned"),
        _e("task.dispatched", {"role": "dev"}),
        _e("dev.build.done"),
        _e("review.rejected", {"reason": "style"}),
    ]
    assert _rework_attempt_ready(events, _T) is True


def test_open_attempt_blocks_rework() -> None:
    """lease 未释放(dispatched 无终局)→ 重派即双写,不许。"""
    events = [
        _e("task.assigned"),
        _e("task.dispatched", {"role": "dev", "fanout_id": "f1"}),
    ]
    assert _rework_attempt_ready(events, _T) is False


def test_plain_failure_allows_rework() -> None:
    events = [
        _e("task.assigned"),
        _e("task.dispatched", {"role": "dev"}),
        _e("dev.failed", {"reason": "boom"}),
    ]
    assert _rework_attempt_ready(events, _T) is True


def test_r5_production_livelock_window_is_suppressed() -> None:
    """钉死在冻结的 avbs-r5 生产事件上(2026-07-04 SCENE-001 活锁窗口,
    56 条真实事件):P2-4 锚定必须压制 rework。fixture 来源
    tests/fixtures/r5-scene001-livelock-window.jsonl(append-only 原文)。"""
    import json
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "r5-scene001-livelock-window.jsonl"
    events = []
    for line in fixture.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        events.append(ZfEvent(
            type=str(raw.get("type") or ""),
            actor=str(raw.get("actor") or ""),
            task_id=str(raw.get("task_id") or "") or None,
            payload=raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
        ))
    assert len(events) >= 45
    assert _rework_attempt_ready(events, "AVBS-SCENE-001") is False
