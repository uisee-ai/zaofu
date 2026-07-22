"""G1:goal idle 续跑驱动器——三条件缺一不发、有界、幂等。"""
from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.goal_idle_driver import GOAL_RESCAN_EVENT, maybe_emit_goal_idle_rescan


class _Writer:
    def __init__(self):
        self.appended: list[ZfEvent] = []

    def append(self, event: ZfEvent) -> ZfEvent:
        self.appended.append(event)
        return event


def _cfg(enabled=True, idle_ticks=2, max_rescans=3):
    return SimpleNamespace(goal=SimpleNamespace(
        enabled=enabled, idle_progress_ticks=idle_ticks, max_rescans=max_rescans,
    ))


def _state():
    return SimpleNamespace(goal_idle_ticks=0, goal_last_progress_event_id="")


def _goal_started() -> ZfEvent:
    return ZfEvent(type="run.goal.started", actor="zf-cli",
                   payload={"objective": "deliver X", "run_id": "r-1"})


def _tick_until_fire(events, cfg, state, writer, ticks=5):
    for _ in range(ticks):
        outcome = maybe_emit_goal_idle_rescan(
            events, config=cfg, state=state, event_writer=writer,
        )
        if outcome:
            return outcome
    return ""


def test_idle_active_goal_fires_bounded_rescan() -> None:
    events = [_goal_started()]
    writer = _Writer()
    outcome = _tick_until_fire(events, _cfg(), _state(), writer)
    assert outcome == "rescan"
    assert writer.appended[0].type == GOAL_RESCAN_EVENT
    assert writer.appended[0].payload["trigger"] == "idle"
    assert writer.appended[0].payload["rescan_ordinal"] == 1


def test_inflight_fanout_blocks_fire() -> None:
    events = [
        _goal_started(),
        ZfEvent(type="fanout.started", actor="zf-cli", payload={"fanout_id": "f-1"}),
    ]
    writer = _Writer()
    assert _tick_until_fire(events, _cfg(), _state(), writer) == ""
    assert not writer.appended


def test_fresh_progress_resets_counter() -> None:
    state = _state()
    cfg = _cfg(idle_ticks=2)
    writer = _Writer()
    events = [_goal_started()]
    assert maybe_emit_goal_idle_rescan(events, config=cfg, state=state, event_writer=writer) == ""
    # 新进展事件出现 → 计数清零
    events = events + [ZfEvent(type="dev.build.done", actor="dev", payload={})]
    assert maybe_emit_goal_idle_rescan(events, config=cfg, state=state, event_writer=writer) == ""
    assert state.goal_idle_ticks == 0


def test_pending_rescan_not_duplicated() -> None:
    events = [
        _goal_started(),
        ZfEvent(type=GOAL_RESCAN_EVENT, actor="zf-cli", payload={"trigger": "idle"}),
    ]
    writer = _Writer()
    assert _tick_until_fire(events, _cfg(), _state(), writer) == ""
    assert not writer.appended


def test_completed_rescan_allows_next_bounded_rescan() -> None:
    request = ZfEvent(
        id="rescan-1",
        type=GOAL_RESCAN_EVENT,
        actor="zf-cli",
        payload={"trigger": "idle", "rescan_ordinal": 1},
    )
    events = [
        _goal_started(),
        request,
        ZfEvent(
            type="goal.rescan.completed",
            actor="zf-cli",
            causation_id=request.id,
            payload={"request_event_id": request.id, "outcome": "no_eligible_tasks"},
        ),
    ]
    writer = _Writer()

    assert _tick_until_fire(events, _cfg(), _state(), writer) == "rescan"
    assert writer.appended[0].payload["rescan_ordinal"] == 2


def test_settled_rescans_reach_human_escalation_cap() -> None:
    events = [_goal_started()]
    for ordinal in range(1, 4):
        request = ZfEvent(
            id=f"rescan-{ordinal}",
            type=GOAL_RESCAN_EVENT,
            actor="zf-cli",
            payload={"trigger": "idle", "rescan_ordinal": ordinal},
        )
        events.extend([
            request,
            ZfEvent(
                type="goal.rescan.failed",
                actor="zf-cli",
                causation_id=request.id,
                payload={
                    "request_event_id": request.id,
                    "outcome": "no_live_lane_delivery",
                },
            ),
        ])
    writer = _Writer()

    assert _tick_until_fire(
        events, _cfg(max_rescans=3), _state(), writer,
    ) == "exhausted"
    assert writer.appended[0].type == "human.escalate"


def test_exhausted_escalates_once() -> None:
    rescans = [
        ZfEvent(type=GOAL_RESCAN_EVENT, actor="zf-cli", payload={"trigger": "idle"})
        for _ in range(3)
    ]
    # 每个 rescan 后有进展又停(pending 被进展清掉),用一条进展事件收尾
    events = [_goal_started(), *rescans,
              ZfEvent(type="dev.build.done", actor="dev", payload={})]
    writer = _Writer()
    state = _state()
    cfg = _cfg(max_rescans=3, idle_ticks=2)
    outcome = _tick_until_fire(events, cfg, state, writer)
    assert outcome == "exhausted"
    assert writer.appended[0].type == "human.escalate"
    assert writer.appended[0].payload["source"] == "goal_idle_driver"
    # 幂等:同一状态不再重复 escalate
    events = events + [writer.appended[0]]
    writer2 = _Writer()
    assert _tick_until_fire(events, cfg, _state(), writer2) == ""
    assert not writer2.appended


def test_disabled_or_non_active_goal_never_fires() -> None:
    writer = _Writer()
    assert _tick_until_fire([_goal_started()], _cfg(enabled=False), _state(), writer) == ""
    events = [
        _goal_started(),
        ZfEvent(type="run.goal.completed", actor="zf-cli", payload={}),
    ]
    assert _tick_until_fire(events, _cfg(), _state(), writer) == ""
    assert not writer.appended
