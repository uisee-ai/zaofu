"""批B:lane 微环——拒收→活会话续改;停滞/死 lane/关闸回退全价路径。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.candidate_rework import plan_candidate_rework
from zf.runtime.lane_micro_loop import (
    CONTINUATION_EVENT,
    GOAL_RESCAN_COMPLETED_EVENT,
    GOAL_RESCAN_FAILED_EVENT,
    maybe_inject_rescan_continuation,
    maybe_inject_rework_continuation,
)
from zf.runtime.run_manager_rework_triage import pending_rework_triage_actions

_TASK = "T-1"


class _Transport:
    def __init__(self, alive: bool = True):
        self.alive = alive
        self.sent: list[tuple[str, str]] = []

    def is_alive(self, lane: str) -> bool:
        return self.alive

    def send_task(self, lane, briefing_path, prompt, *, context=None):
        self.sent.append((lane, str(briefing_path)))


class _TaskStore:
    def __init__(self, assigned: str = "dev-lane-0", status: str = "in_progress"):
        self._task = SimpleNamespace(id=_TASK, assigned_to=assigned, status=status)

    def get(self, task_id):
        return self._task

    def list_all(self):
        return [self._task]

    def update(self, task_id, **changes):
        assert task_id == self._task.id
        for key, value in changes.items():
            setattr(self._task, key, value)


def _cfg(enabled=True, micro=True):
    return SimpleNamespace(goal=SimpleNamespace(
        enabled=enabled, micro_loop=micro,
    ))


def _rejection(eid: str = "rej-1", message: str = "cancelled not terminal") -> ZfEvent:
    return ZfEvent(
        type="verify.failed", id=eid, actor="zf-cli",
        payload={
            "pdd_id": "PDD-1",
            "failed_task_ids": [_TASK],
            "reason": message,
            "findings": [{"severity": "high", "path": "src/store.ts",
                          "message": message}],
        },
    )


def _env(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log


def test_injects_continuation_into_live_lane(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    transport = _Transport()
    injected = maybe_inject_rework_continuation(
        event=_rejection(), config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert injected == [_TASK]
    lane, briefing = transport.sent[0]
    assert lane == "dev-lane-0"
    text = Path(briefing).read_text(encoding="utf-8")
    assert "REWORK CONTINUATION" in text and "cancelled not terminal" in text
    assert "## Objective" in text
    assert "## Work State" in text
    assert "## Next Move" in text
    marks = [e for e in log.read_all() if e.type == CONTINUATION_EVENT]
    assert marks[0].payload["rework_of"] == "rej-1"
    request = next(e for e in log.read_all() if e.type == "task.rework.requested")
    assert request.payload["dispatch_id"] == request.id
    assert marks[0].payload["finding_ids"] == request.payload["finding_ids"]
    assert "Canonical Attempt Identity" in text


def test_same_fingerprint_uses_canonical_cap_not_one_shot_guard(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    transport = _Transport()
    first = maybe_inject_rework_continuation(
        event=_rejection("rej-1"), config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert first == [_TASK]
    # 同 findings 再拒仍是同一 canonical series 的第二次 bounded attempt。
    second = maybe_inject_rework_continuation(
        event=_rejection("rej-2"), config=_cfg(), state_dir=state_dir,
        events=log.read_all(), event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert second == [_TASK]
    # 第三次命中 role cap，micro-loop 不再自行派发。
    capped = maybe_inject_rework_continuation(
        event=_rejection("rej-3"), config=_cfg(), state_dir=state_dir,
        events=log.read_all(), event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert capped == []
    # findings 变了 → 新 canonical series → 可再注入。
    third = maybe_inject_rework_continuation(
        event=_rejection("rej-4", message="different defect"),
        config=_cfg(), state_dir=state_dir,
        events=log.read_all(), event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert third == [_TASK]
    requests = [e for e in log.read_all() if e.type == "task.rework.requested"]
    assert [event.payload["attempt"] for event in requests] == [1, 2, 1]


def test_same_rejection_replay_does_not_redeliver(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    transport = _Transport()
    rejection = _rejection()
    first = maybe_inject_rework_continuation(
        event=rejection, config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    replay = maybe_inject_rework_continuation(
        event=rejection, config=_cfg(), state_dir=state_dir,
        events=log.read_all(), event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert first == replay == [_TASK]
    assert len(transport.sent) == 1
    events = log.read_all()
    assert [e.type for e in events].count("task.rework.requested") == 1
    assert [e.type for e in events].count(CONTINUATION_EVENT) == 1


def test_dead_lane_requests_exact_resume_and_switch_off_does_nothing(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    assert maybe_inject_rework_continuation(
        event=_rejection(), config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=_Transport(alive=False), task_store=_TaskStore(),
    ) == [_TASK]
    events = log.read_all()
    requests = [event for event in events if event.type == "worker.respawn.requested"]
    assert len(requests) == 1
    assert requests[0].payload["delivery_mode"] == "resume_session"
    assert requests[0].payload["continuation_briefing_ref"]
    assert requests[0].payload["attempt"] == 1
    assert requests[0].payload["finding_ids"]
    assert [event.type for event in events].count("task.rework.requested") == 1
    assert maybe_inject_rework_continuation(
        event=_rejection("rej-off"), config=_cfg(micro=False), state_dir=state_dir,
        events=events, event_writer=EventWriter(log),
        transport=_Transport(), task_store=_TaskStore(),
    ) == []


def test_injection_marks_rejection_handled_for_candidate_rework(tmp_path: Path) -> None:
    # 注入事件带 rework_of → plan_candidate_rework 不再为该拒收出计划
    rejection = _rejection()
    mark = ZfEvent(
        type=CONTINUATION_EVENT, actor="zf-cli", task_id=_TASK,
        payload={"task_id": _TASK, "rework_of": rejection.id,
                 "pdd_id": "PDD-1", "fingerprint": "fp"},
    )
    plans = plan_candidate_rework([rejection, mark], max_attempts=2,
                                  config=_cfg())
    assert plans == []
    plans_without_mark = plan_candidate_rework([rejection], max_attempts=2,
                                               config=_cfg())
    assert len(plans_without_mark) == 1


def test_rescan_consumer_injects_open_tasks(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    transport = _Transport()
    rescan = ZfEvent(type="goal.rescan.requested", actor="zf-cli",
                     payload={"trigger": "idle", "rescan_ordinal": 1,
                              "objective": "deliver X"})
    injected = maybe_inject_rescan_continuation(
        event=rescan, config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert injected == [_TASK]
    text = Path(transport.sent[-1][1]).read_text(encoding="utf-8")
    assert "## Objective" in text
    assert "deliver X" in text
    assert "## Work State" in text
    assert "## Next Move" in text
    # 幂等
    again = maybe_inject_rescan_continuation(
        event=rescan, config=_cfg(), state_dir=state_dir,
        events=log.read_all(), event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert again == []


def test_rescan_skips_done_tasks(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    rescan = ZfEvent(type="goal.rescan.requested", actor="zf-cli",
                     payload={"trigger": "idle"})
    assert maybe_inject_rescan_continuation(
        event=rescan, config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=_Transport(), task_store=_TaskStore(status="done"),
    ) == []
    terminal = log.read_all()
    assert [event.type for event in terminal] == [GOAL_RESCAN_COMPLETED_EVENT]
    assert terminal[0].payload["outcome"] == "no_eligible_tasks"
    assert terminal[0].causation_id == rescan.id
    assert maybe_inject_rescan_continuation(
        event=rescan, config=_cfg(), state_dir=state_dir,
        events=terminal, event_writer=EventWriter(log),
        transport=_Transport(), task_store=_TaskStore(status="done"),
    ) == []
    assert len(log.read_all()) == 1


def test_rescan_dead_lane_emits_failed_terminal(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    rescan = ZfEvent(type="goal.rescan.requested", actor="zf-cli",
                     payload={"trigger": "idle", "rescan_ordinal": 2})

    assert maybe_inject_rescan_continuation(
        event=rescan, config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=_Transport(alive=False), task_store=_TaskStore(),
    ) == []

    terminal = log.read_all()
    assert [event.type for event in terminal] == [GOAL_RESCAN_FAILED_EVENT]
    assert terminal[0].payload["outcome"] == "no_live_lane_delivery"
    assert terminal[0].payload["eligible_task_ids"] == [_TASK]


def test_rescan_disabled_micro_loop_still_settles_request(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    rescan = ZfEvent(type="goal.rescan.requested", actor="zf-cli",
                     payload={"trigger": "idle"})

    assert maybe_inject_rescan_continuation(
        event=rescan, config=_cfg(micro=False), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=_Transport(), task_store=_TaskStore(),
    ) == []

    terminal = log.read_all()
    assert [event.type for event in terminal] == [GOAL_RESCAN_FAILED_EVENT]
    assert terminal[0].payload["outcome"] == "micro_loop_disabled"


class _TwoTaskStore:
    def __init__(self):
        self._tasks = {
            "T-A": SimpleNamespace(id="T-A", assigned_to="dev-lane-0", status="in_progress"),
            "T-B": SimpleNamespace(id="T-B", assigned_to="dev-lane-1", status="in_progress"),
        }
    def get(self, task_id):
        return self._tasks.get(task_id)
    def list_all(self):
        return list(self._tasks.values())
    def update(self, task_id, **changes):
        for key, value in changes.items():
            setattr(self._tasks[task_id], key, value)


def _multi_rejection(eid: str, task_ids: list[str], message: str) -> ZfEvent:
    return ZfEvent(
        type="verify.failed", id=eid, actor="zf-cli",
        payload={"pdd_id": "PDD-1", "failed_task_ids": list(task_ids),
                 "reason": message,
                 "findings": [{"severity": "high", "path": "src/x.ts",
                               "message": message}]},
    )


def test_mixed_rejection_emits_cap_fact_for_capped_task(tmp_path: Path) -> None:
    """ZF-REVIEW-137-B1:混合拒收中已 cap 任务必须产出 task.rework.capped
    交 RM(138 裁决 8),不得因兄弟任务被处理而静默丢弃。"""
    state_dir, log = _env(tmp_path)
    transport = _Transport()
    store = _TwoTaskStore()
    msg = "same fingerprint msg"
    for eid in ("rej-b1", "rej-b2"):
        rejection = _multi_rejection(eid, ["T-B"], msg)
        log.append(rejection)
        maybe_inject_rework_continuation(
            event=rejection, config=_cfg(),
            state_dir=state_dir, events=log.read_all(),
            event_writer=EventWriter(log), transport=transport, task_store=store,
        )
    mixed = _multi_rejection("rej-mixed", ["T-A", "T-B"], msg)
    log.append(mixed)
    handled = maybe_inject_rework_continuation(
        event=mixed,
        config=_cfg(), state_dir=state_dir, events=log.read_all(),
        event_writer=EventWriter(log), transport=transport, task_store=store,
    )
    assert "T-A" in handled
    capped = [e for e in log.read_all()
              if e.type == "task.rework.capped"
              and (e.payload or {}).get("task_id") == "T-B"]
    assert len(capped) == 1, "cap 任务必须产出恰一个 cap 事实"
    assert capped[0].payload["semantic_triage_required"] is True
    assert capped[0].payload["recovery_scope"] == "task"
    assert capped[0].payload["failure_count"] >= 2
    assert len(capped[0].payload["failure_event_ids"]) >= 2
    actions = pending_rework_triage_actions(
        log.read_all(),
        threshold=2,
        stale_seconds=30,
    )
    assert len(actions) == 1
    assert actions[0]["task_id"] == "T-B"
    # 重放同一混合事件:cap 事实幂等
    maybe_inject_rework_continuation(
        event=_multi_rejection("rej-mixed", ["T-A", "T-B"], msg),
        config=_cfg(), state_dir=state_dir, events=log.read_all(),
        event_writer=EventWriter(log), transport=transport, task_store=store,
    )
    capped2 = [e for e in log.read_all()
               if e.type == "task.rework.capped"
               and (e.payload or {}).get("task_id") == "T-B"]
    assert len(capped2) == 1, "重放不得重复 cap 事实"
    replay_actions = pending_rework_triage_actions(
        log.read_all(),
        threshold=2,
        stale_seconds=30,
    )
    assert len(replay_actions) == 1
    assert replay_actions[0]["checkpoint_id"] == actions[0]["checkpoint_id"]


def test_capped_fact_append_failure_is_not_silenced(tmp_path: Path) -> None:
    class _FailingWriter:
        def append(self, event):
            raise OSError("event log unavailable")

    state_dir, log = _env(tmp_path)
    transport = _Transport()
    store = _TwoTaskStore()
    msg = "same fingerprint msg"
    for eid in ("rej-b1", "rej-b2"):
        rejection = _multi_rejection(eid, ["T-B"], msg)
        log.append(rejection)
        maybe_inject_rework_continuation(
            event=rejection,
            config=_cfg(),
            state_dir=state_dir,
            events=log.read_all(),
            event_writer=EventWriter(log),
            transport=transport,
            task_store=store,
        )

    capped = _multi_rejection("rej-cap-write-fails", ["T-B"], msg)
    log.append(capped)
    with pytest.raises(OSError, match="event log unavailable"):
        maybe_inject_rework_continuation(
            event=capped,
            config=_cfg(),
            state_dir=state_dir,
            events=log.read_all(),
            event_writer=_FailingWriter(),
            transport=transport,
            task_store=store,
        )


def test_lane_child_scope_is_task_unique() -> None:
    """ZF-REVIEW-140-B4:同 lane 串行多任务的 child 键必须含 task。"""
    from zf.runtime.orchestrator_fanout import _lane_child_scope

    # feature 级 affinity + 不同任务 → 不同 scope
    a = _lane_child_scope("ai-chat-web", "CHAT-SCAFFOLD-001")
    b = _lane_child_scope("ai-chat-web", "CHAT-MVP-002")
    assert a != b
    assert a == "ai-chat-web-CHAT-SCAFFOLD-001"
    # 缺 task 退回 affinity;缺 affinity 用 task;两者相同不重复拼接
    assert _lane_child_scope("ai-chat-web", "") == "ai-chat-web"
    assert _lane_child_scope("", "CHAT-MVP-002") == "CHAT-MVP-002"
    assert _lane_child_scope("T1", "T1") == "T1"
