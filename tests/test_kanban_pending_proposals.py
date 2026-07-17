"""chat-e2e F2: kanban-agent proposals must survive the originating browser
session — pending list is a ledger fold, approval/dismissal are events."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.kanban_proposals import pending_kanban_proposals
from zf.web.server import create_app


def _proposed(event_id: str, title: str, *, valid: bool = True, action: str = "create-task") -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type="kanban.agent.action.proposed",
        actor="web",
        payload={
            "turn_id": "turn-1",
            "conversation_id": "kanban:p",
            "thread_key": "main",
            "proposal": {
                "action": action,
                "requested_action": action,
                "reason": "operator asked",
                "valid": valid,
                "validation_error": "" if valid else "contract has no behavior/verification after normalization",
                "payload": {"title": title},
            },
        },
    )


def test_proposed_is_pending_until_resolved():
    events = [_proposed("evt-p1", "任务甲"), _proposed("evt-p2", "任务乙", valid=False)]
    items = pending_kanban_proposals(events)
    assert [i["proposal_event_id"] for i in items] == ["evt-p2", "evt-p1"]  # newest first
    assert items[1]["title"] == "任务甲" and items[1]["valid"] is True
    assert items[0]["valid"] is False and "behavior/verification" in items[0]["validation_error"]


def test_dismiss_event_resolves():
    events = [
        _proposed("evt-p1", "任务甲"),
        ZfEvent(type="kanban.agent.proposal.resolved", actor="web",
                payload={"proposal_event_id": "evt-p1", "resolution": "dismissed"}),
    ]
    assert pending_kanban_proposals(events) == []


def test_threaded_task_created_resolves():
    events = [
        _proposed("evt-p1", "任务甲"),
        ZfEvent(type="task.created", actor="web",
                payload={"task": {"id": "TASK-1", "title": "改了标题也认"},
                         "request": {"title": "改了标题也认", "proposal_event_id": "evt-p1"}}),
    ]
    assert pending_kanban_proposals(events) == []


def test_title_fallback_resolves_out_of_band_execution():
    # The chat e2e executed proposals via raw API without threading the id —
    # a same-title task.created still collapses the pending entry.
    events = [
        _proposed("evt-p1", "实现 2048 核心棋盘逻辑"),
        ZfEvent(type="task.created", actor="operator",
                payload={"task": {"id": "TASK-1", "title": "实现 2048 核心棋盘逻辑"},
                         "request": {"title": "实现 2048 核心棋盘逻辑"}}),
    ]
    assert pending_kanban_proposals(events) == []


def test_unrelated_task_created_keeps_pending():
    events = [
        _proposed("evt-p1", "任务甲"),
        ZfEvent(type="task.created", actor="web",
                payload={"task": {"id": "TASK-9", "title": "别的任务"},
                         "request": {"title": "别的任务"}}),
    ]
    assert len(pending_kanban_proposals(events)) == 1


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
    return state_dir


def test_dismiss_controlled_action_emits_resolved(tmp_path: Path):
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(_proposed("evt-p1", "任务甲"))
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir, writer, actor="operator", source="web", surface="web",
    )
    requested = writer.emit("web.action.requested", actor="operator", payload={})

    missing = service.execute(
        action="kanban-proposal-dismiss", requested_action="kanban-proposal-dismiss",
        payload={}, requested=requested,
    )
    assert missing["_status_code"] == 422

    result = service.execute(
        action="kanban-proposal-dismiss", requested_action="kanban-proposal-dismiss",
        payload={"proposal_event_id": "evt-p1"}, requested=requested,
    )
    assert result["ok"] is True
    resolved = [e for e in log.read_all() if e.type == "kanban.agent.proposal.resolved"]
    assert len(resolved) == 1
    assert resolved[0].payload["proposal_event_id"] == "evt-p1"
    assert pending_kanban_proposals(log.read_all()) == []


def test_dismiss_through_web_action_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """r2 e2e regression: the service-level test passed while the WEB route
    rejected the action twice (allowlist, then kernel dispatch mapping) —
    this test walks the real boundary."""
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state_dir = _state(tmp_path)
    EventLog(state_dir / "events.jsonl").append(_proposed("evt-p1", "任务甲"))
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]

    response = client.post(
        f"/api/projects/{project_id}/actions/kanban-proposal-dismiss",
        headers={"x-zf-web-token": "test-token"},
        json={"project_id": project_id, "actor": "operator",
              "payload": {"proposal_event_id": "evt-p1"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    page = client.get(f"/api/projects/{project_id}/kanban-agent/pending-proposals")
    assert page.json()["items"] == []


def test_pending_proposals_endpoint(tmp_path: Path):
    state_dir = _state(tmp_path)
    EventLog(state_dir / "events.jsonl").append(_proposed("evt-p1", "任务甲"))
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]

    page = client.get(f"/api/projects/{project_id}/kanban-agent/pending-proposals")
    assert page.status_code == 200
    items = page.json()["items"]
    assert len(items) == 1 and items[0]["title"] == "任务甲"


def test_executed_non_create_proposal_clears_its_card(tmp_path: Path):
    """frontend-stress (2026-07-15): a NON-create proposal (update-task) that is
    Accepted + executed must clear its Triage card, not linger forever. Only
    create-task cleared before (via task.created) and dismiss (via its own
    resolved) — every other executed proposal stayed pending. execute() now
    emits kanban.agent.proposal.resolved for any successful action carrying a
    proposal_event_id (the Web Accept threads it on every proposal)."""
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir, writer, actor="operator", source="web", surface="web",
    )
    requested = writer.emit("web.action.requested", actor="operator", payload={})

    created = service.execute(
        action="create-task", requested_action="create-task",
        payload={"title": "可更新的任务",
                 "contract": {"behavior": "b", "verification": "v", "scope": ["src/**"]}},
        requested=requested,
    )
    assert created["ok"] is True
    task_id = created["task_id"]

    # a proposed update-task lands as a pending card
    log.append(_proposed("evt-upd", "改优先级", action="update-task"))
    assert any(i["proposal_event_id"] == "evt-upd"
               for i in pending_kanban_proposals(log.read_all())), "update-task card should be pending"

    # Accept + execute the update-task (Web threads proposal_event_id)
    result = service.execute(
        action="update-task", requested_action="update-task",
        payload={"task_id": task_id, "priority": 1, "proposal_event_id": "evt-upd"},
        requested=requested,
    )
    assert result["ok"] is True

    resolved = [e for e in log.read_all()
                if e.type == "kanban.agent.proposal.resolved"
                and e.payload.get("proposal_event_id") == "evt-upd"]
    assert len(resolved) == 1
    assert resolved[0].payload["resolution"] == "executed"
    # the Triage card is now cleared
    assert not any(i["proposal_event_id"] == "evt-upd"
                   for i in pending_kanban_proposals(log.read_all())), "card must clear after execute"
