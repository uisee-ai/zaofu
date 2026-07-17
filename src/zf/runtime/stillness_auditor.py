"""静止审计器(Stillness Auditor)——run 级三向对账 + 断点定位。

07-16/17 两轮 PRD E2E 实弹:七类"tmux 全无活动"背后是七种机器状态,
实际检测者全是操作员(延迟 10-45 分钟)。核心原则:**run 在任何时刻
必须能回答"为什么现在没有事情发生"**;答不上来 = 静默失速。

三态判定(纯读事件账,零 LLM,O(事件数)):

- ACTIVE:存在在飞 fanout child / 活跃 dispatch
- PARKED:存在成文等待理由(未决 escalation / 预算超限 / quiescent)
- STALLED:未竟工作非空 + 无在飞 + 无合法等待 → `run.stalled`
  (事件类型已在 known_types 注册,此前无生产者)

断点定位:对每个"发了但没人消费"的推进事件,按最后一次 loop.started
区分死窗断点(事件落在停机窗,loop 从未见过)与派生断点(loop 活着
但没派生)。死窗断点自动重发一次(带 redrive_of 幂等守卫 + rework_of
代际,B3 修复的代际语义承接);派生断点只报告,交 RM/人。

借鉴 pi-workflow dynamic-decision-loop:digest 兜底——pending 集合
指纹连续多个审计不变且非空,即使断点分类器未命中也判 STALLED。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from zf.core.events.model import ZfEvent

RUN_STALLED_EVENT = "run.stalled"

# 会推进流程、且消费形态可机械核对的事件(v1 白名单;消费证据 =
# fanout.started.trigger_event_id 或 causation 指向)
_DRIVING_EVENT_TYPES = frozenset({
    "task_map.ready",
    "lane.stage.completed",
    "flow.goal.closed",
    "flow.discovery.requested",
    "flow.discovery.completed",
})

_FANOUT_TERMINAL_TYPES = frozenset({
    "fanout.aggregate.completed", "fanout.timed_out", "fanout.cancelled",
})

# 合法等待:近期预算超限视为停车理由的窗口
_BUDGET_PARK_WINDOW_S = 600.0
# 推进事件的消费宽限(loop 唤醒/派生需要时间)
_CONSUMPTION_GRACE_S = 180.0


@dataclass
class StillnessReport:
    state: str  # active | parked | stalled
    reason: str = ""
    breakpoints: list[dict] = field(default_factory=list)
    pending_digest: str = ""


@dataclass
class StillnessState:
    """跨 tick 的 digest 兜底计数(pi-workflow stallCount 同型,带衰减)。"""

    last_digest: str = ""
    unchanged_count: int = 0


def _event_epoch(event: ZfEvent) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(event.ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def audit_stillness(
    events: Iterable[ZfEvent],
    *,
    now_epoch: float,
    state: StillnessState | None = None,
    digest_stall_threshold: int = 3,
) -> StillnessReport:
    events = list(events)
    inflight_fanouts: dict[str, str] = {}
    consumed_triggers: set[str] = set()
    driving: dict[str, ZfEvent] = {}
    redriven: set[str] = set()
    open_escalations = 0
    last_budget_epoch = 0.0
    last_loop_started_epoch = 0.0
    quiescent = False

    for event in events:
        etype = event.type
        payload = event.payload if isinstance(event.payload, dict) else {}
        if etype == "fanout.started":
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                inflight_fanouts[fanout_id] = event.id
            trigger = str(payload.get("trigger_event_id") or "")
            if trigger:
                consumed_triggers.add(trigger)
        elif etype in _FANOUT_TERMINAL_TYPES:
            inflight_fanouts.pop(str(payload.get("fanout_id") or ""), None)
        elif etype == "candidate.rework.quarantined":
            # 隔离 = 已消费(有意压制),不算断链
            trigger = str(payload.get("trigger_event_id") or "")
            if trigger:
                consumed_triggers.add(trigger)
        elif etype in _DRIVING_EVENT_TYPES:
            driving[event.id] = event
        elif etype == "human.escalate":
            open_escalations += 1
        elif etype == "human.escalation.acknowledged":
            open_escalations = max(0, open_escalations - 1)
        elif etype == "cost.budget.exceeded":
            last_budget_epoch = max(last_budget_epoch, _event_epoch(event))
        elif etype in ("loop.started", "session.started"):
            last_loop_started_epoch = max(
                last_loop_started_epoch, _event_epoch(event),
            )
        elif etype == "run.goal.quiescent.entered":
            quiescent = True
        elif etype == "run.goal.quiescent.exited":
            quiescent = False
        # 因果消费:closure/goal 族以 causation 指向触发事件
        causation = str(getattr(event, "causation_id", "") or "")
        if causation:
            consumed_triggers.add(causation)
        redrive_of = str(payload.get("redrive_of") or "")
        if redrive_of:
            redriven.add(redrive_of)

    if inflight_fanouts:
        return StillnessReport(state="active", reason="inflight_fanouts")

    # 未消费的推进事件(过宽限窗)
    breakpoints: list[dict] = []
    for event_id, event in driving.items():
        if event_id in consumed_triggers or event_id in redriven:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "lane.stage.completed"
            and not str(payload.get("next_stage_slot") or "")
        ):
            continue  # 末段完成本就无下游
        age = now_epoch - _event_epoch(event)
        if age < _CONSUMPTION_GRACE_S:
            continue
        kind = (
            "dead_window"
            if _event_epoch(event) < last_loop_started_epoch
            else "derivation_gap"
        )
        breakpoints.append({
            "breakpoint": kind,
            "event_id": event_id,
            "event_type": event.type,
            "age_seconds": round(age, 1),
        })

    pending_digest = hashlib.sha256(json.dumps(
        sorted(bp["event_id"] for bp in breakpoints),
    ).encode()).hexdigest()[:16] if breakpoints else ""

    if state is not None:
        if breakpoints and pending_digest == state.last_digest:
            state.unchanged_count += 1
        else:
            state.unchanged_count = max(0, state.unchanged_count - 1)
        state.last_digest = pending_digest

    if not breakpoints:
        return StillnessReport(state="parked" if (
            quiescent or open_escalations
            or now_epoch - last_budget_epoch < _BUDGET_PARK_WINDOW_S
        ) else "active", reason="no_pending_work")

    if quiescent:
        return StillnessReport(
            state="parked", reason="quiescent",
            breakpoints=breakpoints, pending_digest=pending_digest,
        )
    if open_escalations:
        return StillnessReport(
            state="parked", reason="open_escalation",
            breakpoints=breakpoints, pending_digest=pending_digest,
        )
    if now_epoch - last_budget_epoch < _BUDGET_PARK_WINDOW_S:
        return StillnessReport(
            state="parked", reason="budget_exceeded",
            breakpoints=breakpoints, pending_digest=pending_digest,
        )

    return StillnessReport(
        state="stalled",
        reason="pending_without_driver",
        breakpoints=breakpoints,
        pending_digest=pending_digest,
    )


def emit_stalled_and_redrive(
    event_writer: Any,
    events: Iterable[ZfEvent],
    report: StillnessReport,
) -> dict[str, int]:
    """落账 run.stalled(按 digest 幂等)并重发死窗断点(每源一次)。"""

    events = list(events)
    emitted = {"stalled": 0, "redriven": 0}
    already = any(
        e.type == RUN_STALLED_EVENT
        and isinstance(e.payload, dict)
        and str(e.payload.get("pending_digest") or "") == report.pending_digest
        for e in events
    )
    if not already:
        event_writer.append(ZfEvent(
            type=RUN_STALLED_EVENT,
            actor="zf-cli",
            payload={
                "schema_version": "run-stalled.v1",
                "reason": report.reason,
                "breakpoints": report.breakpoints,
                "pending_digest": report.pending_digest,
            },
        ))
        emitted["stalled"] += 1

    by_id = {e.id: e for e in events}
    for bp in report.breakpoints:
        if bp.get("breakpoint") != "dead_window":
            continue  # 派生断点只报告:loop 活着却不派生可能是有意压制
        original = by_id.get(str(bp.get("event_id") or ""))
        if original is None:
            continue
        payload = dict(
            original.payload if isinstance(original.payload, dict) else {},
        )
        payload["redrive_of"] = original.id
        payload["rework_of"] = original.id  # 代际语义:redrive 不是 replay
        event_writer.append(ZfEvent(
            type=original.type,
            actor="zf-cli",
            task_id=original.task_id,
            payload=payload,
            causation_id=original.id,
            correlation_id=original.correlation_id,
        ))
        emitted["redriven"] += 1
    return emitted


__all__ = [
    "RUN_STALLED_EVENT",
    "StillnessReport",
    "StillnessState",
    "audit_stillness",
    "emit_stalled_and_redrive",
]
