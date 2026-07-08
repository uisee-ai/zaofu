"""U3/G3:escalate 后静默(quiescent)与唤醒即恢复。

r6.1 实弹:终局 escalate 后 kernel 继续 probe/drift/tick 全速空烧
(4h 烧 6.4M;r5 撞限 12h 同族)。借 codex 语义:非 Active 状态循环
天然不点火,恢复(操作员动作/新进展)即自动重新点火。

机械规则:最近一次 human.escalate 之后既无进展事件也无唤醒事件,且
已过宽限窗(给 RM 自愈周期留路——r6.1 续跑里 escalate 后 1 分钟内
replan 出进展的自愈路径不受影响)→ 静默:tick 服务全体跳过。
灰度:goal.enabled 且 goal.quiescent_after_escalate,默认关=零回归。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from zf.core.events.model import ZfEvent

QUIESCENT_ENTERED_EVENT = "run.goal.quiescent.entered"
QUIESCENT_EXITED_EVENT = "run.goal.quiescent.exited"
_ESCALATE_EVENT = "human.escalate"
_GRACE_SECONDS = 600.0

# 唤醒事件:操作员/外部动作(kernel 自噪音不算——workflow resume 等
# 由 tick 服务自身产生,若算唤醒则静默永不生效)
_WAKE_EVENT_TYPES = frozenset({
    "user.message",
    "user.intent.submitted",
    "runtime.resume.requested",
    "runtime.attention.acknowledged",
    "run.goal.updated",
    "dispatch.resumed",
    "loop.resume_requested",
})


@dataclass(frozen=True)
class QuiescentStatus:
    quiescent: bool
    reason: str = ""
    escalate_event_id: str = ""


def _progress_events() -> frozenset[str]:
    from zf.runtime.run_manager import _PROGRESS_SUCCESS_EVENTS

    return _PROGRESS_SUCCESS_EVENTS


def _event_epoch(event: ZfEvent) -> float:
    try:
        return datetime.fromisoformat(str(event.ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _enabled(config: Any) -> bool:
    goal = getattr(config, "goal", None)
    return bool(
        getattr(goal, "enabled", False)
        and getattr(goal, "quiescent_after_escalate", True)
    )


def quiescent_now(
    events: Iterable[ZfEvent],
    *,
    config: Any,
    now_epoch: float,
) -> QuiescentStatus:
    if not _enabled(config):
        return QuiescentStatus(quiescent=False, reason="disabled")
    escalate: ZfEvent | None = None
    woke_after = False
    progress = _progress_events()
    inflight_fanouts: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        if event.type == "fanout.started" and fanout_id:
            inflight_fanouts.add(fanout_id)
        elif fanout_id and event.type in {
            "fanout.aggregate.completed", "fanout.timed_out", "fanout.cancelled",
        }:
            inflight_fanouts.discard(fanout_id)
        if event.type == _ESCALATE_EVENT:
            escalate = event
            woke_after = False
            continue
        if escalate is None:
            continue
        if event.type in progress or event.type in _WAKE_EVENT_TYPES:
            woke_after = True
    if escalate is None:
        return QuiescentStatus(quiescent=False, reason="no_escalate")
    if woke_after:
        return QuiescentStatus(quiescent=False, reason="woken")
    if inflight_fanouts:
        # E8(prd-goal e2e finding-18):escalate 后仍有未终局 fanout
        # (含待派发 child)→ 静默会暂停派发扫描,潜在互锁。有活干
        # 就不静默;E6 超时地板保证 zombie 不会把此闸永久钉开。
        return QuiescentStatus(quiescent=False, reason="inflight_fanouts")
    age = now_epoch - _event_epoch(escalate)
    if age < _GRACE_SECONDS:
        return QuiescentStatus(quiescent=False, reason="grace_window")
    return QuiescentStatus(
        quiescent=True,
        reason="escalate_unresolved",
        escalate_event_id=escalate.id,
    )


def mark_quiescent_transition(
    event_writer: Any,
    events: Iterable[ZfEvent],
    *,
    status: QuiescentStatus,
) -> bool:
    """entered/exited 事件各发一次(按最近状态去重)。返回是否发射。"""
    last = ""
    last_escalate_ref = ""
    for event in events:
        if event.type in {QUIESCENT_ENTERED_EVENT, QUIESCENT_EXITED_EVENT}:
            last = event.type
            payload = event.payload if isinstance(event.payload, dict) else {}
            last_escalate_ref = str(payload.get("escalate_event_id") or "")
    if status.quiescent:
        if last == QUIESCENT_ENTERED_EVENT and last_escalate_ref == status.escalate_event_id:
            return False
        event_writer.append(ZfEvent(
            type=QUIESCENT_ENTERED_EVENT,
            actor="zf-cli",
            payload={
                "reason": status.reason,
                "escalate_event_id": status.escalate_event_id,
            },
            causation_id=status.escalate_event_id or None,
        ))
        return True
    if last == QUIESCENT_ENTERED_EVENT:
        event_writer.append(ZfEvent(
            type=QUIESCENT_EXITED_EVENT,
            actor="zf-cli",
            payload={"reason": status.reason},
        ))
        return True
    return False


__all__ = [
    "QUIESCENT_ENTERED_EVENT",
    "QUIESCENT_EXITED_EVENT",
    "QuiescentStatus",
    "mark_quiescent_transition",
    "quiescent_now",
]
