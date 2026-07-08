"""G1(133):goal 续跑驱动器——idle 检测与有界 rescan 点火。

codex idle-continuation 的 kernel 侧移植:goal 仍 active 而系统空转
(无在途 fanout、连续 N 个服务 tick 无进展事件)时,机械发射
`goal.rescan.requested{trigger:idle}`;语义判断(缺口是什么/怎么换
路径)留给消费侧 agent。有界:`goal.max_rescans` 穷尽后升级 human
(133-G2 三级出口的第三级)。灰度 goal.enabled,默认关。

收编约束(133 §4/G1 硬性):本驱动器只点火 rescan,不作废任何在途
交付——换代重扇出必须与 BF-1 收编语义组合,由消费侧遵守。
"""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent

GOAL_RESCAN_EVENT = "goal.rescan.requested"
_FANOUT_TERMINALS = frozenset({
    "fanout.aggregate.completed", "fanout.timed_out", "fanout.cancelled",
})
_FAIL_EVENT_SUFFIXES = (".rejected", ".failed")


def _enabled(config: Any) -> bool:
    return bool(getattr(getattr(config, "goal", None), "enabled", False))


def maybe_emit_goal_idle_rescan(
    events: list[ZfEvent],
    *,
    config: Any,
    state: Any,
    event_writer: Any,
) -> str:
    """返回 ""(未点火)/ "rescan" / "exhausted"。

    state 需携带 goal_idle_ticks / goal_last_progress_event_id 两个
    可变字段(TickServiceState)。
    """
    if not _enabled(config):
        return ""
    from zf.runtime.run_manager import (
        _PROGRESS_SUCCESS_EVENTS,
        build_run_goal_projection,
    )

    # idle 判定的进展面比 RM 工作流进展面宽:交付/集成/审结也算"在动"
    progress_events = _PROGRESS_SUCCESS_EVENTS | {
        "dev.build.done", "fanout.child.completed",
        "candidate.ready", "review.approved",
    }
    projection = build_run_goal_projection(events)
    if str(projection.get("status")) != "active":
        state.goal_idle_ticks = 0
        return ""

    inflight: set[str] = set()
    last_progress_id = ""
    pending_rescan = False
    rescan_count = 0
    fail_refs: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        if event.type == "fanout.started" and fanout_id:
            inflight.add(fanout_id)
        elif event.type in _FANOUT_TERMINALS and fanout_id:
            inflight.discard(fanout_id)
        if event.type in progress_events:
            last_progress_id = event.id
            pending_rescan = False
        if event.type == GOAL_RESCAN_EVENT:
            rescan_count += 1
            pending_rescan = True
        if event.type.endswith(_FAIL_EVENT_SUFFIXES) and not event.type.startswith("codex."):
            fail_refs.append(event.id)

    if inflight or pending_rescan:
        state.goal_idle_ticks = 0
        return ""
    if last_progress_id != state.goal_last_progress_event_id:
        state.goal_last_progress_event_id = last_progress_id
        state.goal_idle_ticks = 0
        return ""
    state.goal_idle_ticks += 1
    idle_needed = int(getattr(config.goal, "idle_progress_ticks", 3) or 3)
    if state.goal_idle_ticks < idle_needed:
        return ""
    state.goal_idle_ticks = 0
    max_rescans = int(getattr(config.goal, "max_rescans", 5) or 0)
    if rescan_count >= max_rescans:
        # 有界:穷尽后升级 human(quiescent 会在宽限后接管静默)
        if _exhausted_already_escalated(events):
            return ""
        event_writer.append(ZfEvent(
            type="human.escalate",
            actor="zf-cli",
            payload={
                "reason": "goal idle rescans exhausted",
                "source": "goal_idle_driver",
                "rescan_count": rescan_count,
                "max_rescans": max_rescans,
            },
        ))
        return "exhausted"
    event_writer.append(ZfEvent(
        type=GOAL_RESCAN_EVENT,
        actor="zf-cli",
        payload={
            "trigger": "idle",
            "source": "goal_idle_driver",
            "rescan_ordinal": rescan_count + 1,
            "max_rescans": max_rescans,
            "objective": str(projection.get("objective") or ""),
            "findings_refs": fail_refs[-5:],
        },
    ))
    return "rescan"


def _exhausted_already_escalated(events: Iterable[ZfEvent]) -> bool:
    last_rescan_seen = False
    escalated_after = False
    for event in events:
        if event.type == GOAL_RESCAN_EVENT:
            last_rescan_seen = True
            escalated_after = False
        elif event.type == "human.escalate":
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("source") or "") == "goal_idle_driver":
                escalated_after = True
    return last_rescan_seen and escalated_after


__all__ = ["GOAL_RESCAN_EVENT", "maybe_emit_goal_idle_rescan"]
