from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events import EventLog, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.kanban_agent_summary import project_kanban_agent_summary
from zf.runtime.operator_intent import infer_operator_intent
from zf.web.server import create_app


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
    return state_dir


def test_operator_intent_classifier_marks_high_risk_actions() -> None:
    status = infer_operator_intent("当前项目状态怎么样", project_id="demo")
    assert status["intent_type"] == "project_status_query"
    assert status["requires_owner_approval"] is False
    assert status["proposed_actions"] == []

    restart = infer_operator_intent("重启 runtime 并恢复工作流", project_id="demo")
    assert restart["intent_type"] == "runtime_restart"
    assert restart["risk"] == "high"
    assert restart["requires_owner_approval"] is True
    assert restart["proposed_actions"] == ["runtime-restart"]

    product = infer_operator_intent("把这个 idea 跑成产品", project_id="demo")
    assert product["intent_type"] == "idea_to_product"
    assert product["proposed_actions"] == ["create-task", "workflow-invoke"]


def test_project_kanban_agent_summary_is_read_only(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-TODO", title="todo", status="backlog"))
    store.add(Task(id="TASK-RUN", title="run", status="in_progress", assigned_to="dev"))
    store.add(Task(id="TASK-BLOCK", title="blocked", status="blocked"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="workflow.invoke.requested",
        actor="test",
        task_id="TASK-RUN",
        payload={"pattern_id": "dag"},
    ))
    before_events = (state_dir / "events.jsonl").read_text(encoding="utf-8")

    summary = project_kanban_agent_summary(
        state_dir,
        project_root=tmp_path,
        project_id="demo",
        include_pane_probe=False,
    )

    assert summary["schema_version"] == "kanban-agent.project-summary.v0"
    assert summary["is_derived_projection"] is True
    assert summary["tasks"]["by_board_column"]["todo"] == 1
    assert summary["tasks"]["by_board_column"]["in_progress"] == 1
    assert summary["tasks"]["by_board_column"]["blocked"] == 1
    assert summary["workflow"]["latest"][-1]["type"] == "workflow.invoke.requested"
    assert any(item["kind"] == "inspect_blocked_tasks" for item in summary["next_actions"])
    assert (state_dir / "events.jsonl").read_text(encoding="utf-8") == before_events


def test_project_kanban_agent_summary_explains_replan_loop(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="replan.proposal.created",
        actor="autoresearch",
        payload={
            "proposal_ref": ".zf/autoresearch/replan.json",
            "candidate_task_map_ref": "tm-v2",
            "reason": "plan gap",
        },
    ))
    log.append(ZfEvent(
        type="replan.contract_eval.completed",
        actor="zf-replan-eval",
        payload={
            "eval_id": "eval-1",
            "decision": "revise",
            "reason": "missing coverage",
            "eval": {"new_task_map_ref": "tm-v2"},
        },
    ))

    summary = project_kanban_agent_summary(
        state_dir,
        project_root=tmp_path,
        project_id="demo",
        include_pane_probe=False,
    )

    assert summary["replan_loop"]["status"] == "owner_review"
    assert summary["replan_loop"]["pending_owner_review"] >= 1
    assert summary["replan_loop"]["latest"][-1]["decision"] == "revise"
    assert summary["replan_loop"]["latest"][-1]["candidate_task_map_ref"] == "tm-v2"


def test_kanban_agent_summary_api_supports_default_and_project_scope(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-1", title="hello", status="backlog"))
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    default_summary = client.get("/api/kanban-agent/summary")
    assert default_summary.status_code == 200
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]
    scoped_summary = client.get(f"/api/projects/{project_id}/kanban-agent/summary")

    assert scoped_summary.status_code == 200
    assert scoped_summary.json()["tasks"]["total"] == default_summary.json()["tasks"]["total"]


def test_project_operator_actions_emit_proposals_without_mutating_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state_dir = _state(tmp_path)
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    intent = client.post(
        "/api/actions/operator.intent.create",
        headers={"x-zf-web-token": "test-token"},
        json={"message": "把这个 idea 跑成产品", "project_id": "demo"},
    )
    assert intent.status_code == 202
    assert intent.json()["intent_type"] == "idea_to_product"

    proposal = client.post(
        "/api/actions/idea-to-product",
        headers={"x-zf-web-token": "test-token"},
        json={"objective": "开发一个可交付的 Data Agent", "project_id": "demo"},
    )
    assert proposal.status_code == 202
    assert proposal.json()["status"] == "proposed"
    assert TaskStore(state_dir / "kanban.json").list_all() == []

    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [event.type for event in events]
    assert "operator.intent.created" in types
    assert "operator.action.proposed" in types


def test_runtime_restart_requires_approval_or_proposal_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state_dir = _state(tmp_path)
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    blocked = client.post(
        "/api/actions/runtime.restart",
        headers={"x-zf-web-token": "test-token"},
        json={"reason": "operator requested restart"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["status"] == "approval_required"

    proposed = client.post(
        "/api/actions/runtime.restart",
        headers={"x-zf-web-token": "test-token"},
        json={"reason": "operator requested restart", "proposal_only": True},
    )
    assert proposed.status_code == 202
    assert proposed.json()["status"] == "proposed"
    assert proposed.json()["event_type"] == "runtime.restart.proposed"
    event = [
        item for item in EventLog(state_dir / "events.jsonl").read_all()
        if item.type == "runtime.restart.proposed"
    ][-1]
    assert event.payload["impact_summary"]["direct_tmux_kill_allowed"] is False
