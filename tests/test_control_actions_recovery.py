"""恢复手术动词:前置条件即护栏(agent 裁决错也不伤真相)。"""
from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService


def _service(tmp_path: Path) -> ControlledActionService:
    state = tmp_path / ".zf"
    state.mkdir(parents=True, exist_ok=True)
    (state / "kanban.json").write_text(json.dumps([
        {"id": "T1", "title": "t", "status": "in_progress",
         "assigned_to": "dev-lane-0"},
        {"id": "T2", "title": "t2", "status": "done", "assigned_to": ""},
    ]))
    log = EventLog(state / "events.jsonl")
    return ControlledActionService(state, EventWriter(log), actor="zf-cli")


def _req(payload: dict) -> ZfEvent:
    return ZfEvent(type="controlled.action.requested", actor="web", payload=payload)


def test_task_requeue_requires_dead_carrier(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    # 活 child 在飞 → 拒绝
    log.append(ZfEvent(type="fanout.child.dispatched", actor="zf-cli",
                       payload={"task_id": "T1", "fanout_id": "f1", "child_id": "c1"}))
    r = svc._task_requeue_action(
        requested=_req({}), action="task-requeue",
        requested_action="task-requeue", payload={"task_id": "T1"},
    )
    assert r["ok"] is False and "live fanout child" in r["reason"]
    # child 死 → 放行
    log.append(ZfEvent(type="fanout.child.failed", actor="zf-cli",
                       payload={"task_id": "T1", "fanout_id": "f1", "child_id": "c1"}))
    r2 = svc._task_requeue_action(
        requested=_req({}), action="task-requeue",
        requested_action="task-requeue", payload={"task_id": "T1"},
    )
    assert r2["ok"] is True
    assert any(e.type == "task.requeued" for e in log.read_all())


def test_child_rebuild_preconditions(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    # 无历史 child → 拒
    r = svc._child_rebuild_action(
        requested=_req({}), action="child-rebuild",
        requested_action="child-rebuild", payload={"task_id": "T1"},
    )
    assert r["ok"] is False
    # done 任务 → 拒
    r2 = svc._child_rebuild_action(
        requested=_req({}), action="child-rebuild",
        requested_action="child-rebuild", payload={"task_id": "T2"},
    )
    assert r2["ok"] is False
    # 死 child → 放行,发 task.rework.requested 带 rework_of
    log.append(ZfEvent(type="fanout.child.failed", actor="zf-cli",
                       payload={"task_id": "T1", "fanout_id": "f1", "child_id": "c9"}))
    r3 = svc._child_rebuild_action(
        requested=_req({}), action="child-rebuild",
        requested_action="child-rebuild", payload={"task_id": "T1"},
    )
    assert r3["ok"] is True
    rw = [e for e in log.read_all() if e.type == "task.rework.requested"]
    assert rw and rw[-1].payload["rework_of"] == "c9"


def test_stage_retrigger_idempotent_and_generational(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    src = svc.writer.append(ZfEvent(type="task_map.ready", actor="zf-cli",
                             payload={"task_map_ref": "x"}))
    r = svc._stage_retrigger_action(
        requested=_req({}), action="stage-retrigger",
        requested_action="stage-retrigger",
        payload={"source_event_id": src.id},
    )
    assert r["ok"] is True
    re = [e for e in log.read_all()
          if e.type == "task_map.ready" and (e.payload or {}).get("redrive_of") == src.id]
    assert len(re) == 1 and re[0].payload["rework_of"] == src.id
    # 第二次同源 → 幂等拒绝
    r2 = svc._stage_retrigger_action(
        requested=_req({}), action="stage-retrigger",
        requested_action="stage-retrigger",
        payload={"source_event_id": src.id},
    )
    assert r2["ok"] is False and "already retriggered" in r2["reason"]
    # 非推进事件 → 拒
    other = svc.writer.append(ZfEvent(type="worker.heartbeat", actor="w", payload={}))
    r3 = svc._stage_retrigger_action(
        requested=_req({}), action="stage-retrigger",
        requested_action="stage-retrigger",
        payload={"source_event_id": other.id},
    )
    assert r3["ok"] is False


def test_rescan_grant_requires_exhaustion(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    r = svc._rescan_grant_action(
        requested=_req({}), action="rescan-grant",
        requested_action="rescan-grant", payload={},
    )
    assert r["ok"] is False  # 无弹尽记录
    log.append(ZfEvent(type="human.escalate", actor="zf-cli",
                       payload={"reason": "goal idle rescans exhausted"}))
    r2 = svc._rescan_grant_action(
        requested=_req({}), action="rescan-grant",
        requested_action="rescan-grant", payload={},
    )
    assert r2["ok"] is True
    assert any(e.type == "run.goal.rescan.granted" for e in log.read_all())
    # 冷却内再来 → 拒
    r3 = svc._rescan_grant_action(
        requested=_req({}), action="rescan-grant",
        requested_action="rescan-grant", payload={},
    )
    assert r3["ok"] is False
