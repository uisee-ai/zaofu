"""批B:lane 微环——拒收→活会话续改;停滞/死 lane/关闸回退全价路径。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.candidate_rework import plan_candidate_rework
from zf.runtime.lane_micro_loop import (
    CONTINUATION_EVENT,
    maybe_inject_rescan_continuation,
    maybe_inject_rework_continuation,
)

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


def test_same_fingerprint_second_rejection_falls_back(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    transport = _Transport()
    first = maybe_inject_rework_continuation(
        event=_rejection("rej-1"), config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert first == [_TASK]
    # 同 findings 再拒 → 停滞 → 不再注入(回退全价重派)
    second = maybe_inject_rework_continuation(
        event=_rejection("rej-2"), config=_cfg(), state_dir=state_dir,
        events=log.read_all(), event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert second == []
    # findings 变了 → 新指纹 → 可再注入
    third = maybe_inject_rework_continuation(
        event=_rejection("rej-3", message="different defect"),
        config=_cfg(), state_dir=state_dir,
        events=log.read_all(), event_writer=EventWriter(log),
        transport=transport, task_store=_TaskStore(),
    )
    assert third == [_TASK]


def test_dead_lane_or_switch_off_no_injection(tmp_path: Path) -> None:
    state_dir, log = _env(tmp_path)
    assert maybe_inject_rework_continuation(
        event=_rejection(), config=_cfg(), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
        transport=_Transport(alive=False), task_store=_TaskStore(),
    ) == []
    assert maybe_inject_rework_continuation(
        event=_rejection(), config=_cfg(micro=False), state_dir=state_dir,
        events=[], event_writer=EventWriter(log),
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
