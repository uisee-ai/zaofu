"""chat-e2e F2: kanban-agent proposals must survive the originating browser
session — pending list is a ledger fold, approval/dismissal are events."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.kanban_proposals import (
    pending_kanban_proposals,
    proposal_execution_gate,
)
from zf.runtime.workflow_anchor import (
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_origin import workflow_origin_digest
from zf.web.proposal_extraction import extract_action_proposal
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
    # Legacy raw API callers did not thread proposal IDs or source lineage;
    # retain a title fallback for that unbound compatibility path.
    events = [
        _proposed("evt-p1", "实现 2048 核心棋盘逻辑"),
        ZfEvent(type="task.created", actor="operator",
                payload={"task": {"id": "TASK-1", "title": "实现 2048 核心棋盘逻辑"},
                         "request": {"title": "实现 2048 核心棋盘逻辑"}}),
    ]
    assert pending_kanban_proposals(events) == []


def test_title_fallback_does_not_hide_distinct_channel_prd_lineage():
    proposal = _proposed("evt-p1", "HKIA Delivery")
    proposal.payload["proposal"]["payload"] = {
        "title": "HKIA Delivery",
        "contract": {
            "source_ref": "channels/new/prd/r1.json",
            "evidence_contract": {
                "channel_id": "ch-new",
                "channel_prd_digest": "digest-new",
            },
        },
    }
    events = [
        proposal,
        ZfEvent(
            type="task.created",
            actor="operator",
            payload={
                "task": {
                    "id": "TASK-OLD",
                    "title": "HKIA Delivery",
                    "contract": {
                        "source_ref": "channels/old/prd/r1.json",
                        "evidence_contract": {
                            "channel_id": "ch-old",
                            "channel_prd_digest": "digest-old",
                        },
                    },
                },
                "request": {"title": "HKIA Delivery"},
            },
        ),
    ]

    assert [item["proposal_event_id"] for item in pending_kanban_proposals(events)] == ["evt-p1"]


def test_unrelated_task_created_keeps_pending():
    events = [
        _proposed("evt-p1", "任务甲"),
        ZfEvent(type="task.created", actor="web",
                payload={"task": {"id": "TASK-9", "title": "别的任务"},
                         "request": {"title": "别的任务"}}),
    ]
    assert len(pending_kanban_proposals(events)) == 1


def test_agent_cannot_bypass_task_workflow_plan_with_direct_proposal():
    proposal = extract_action_proposal(
        (
            '```json\n{"action_proposal":{"action":"task-workflow-start",'
            '"payload":{"task_id":"TASK-1","route_id":"research:fixed",'
            '"objective":"Research"},"reason":"start"}}\n```'
        ),
        user_message="start the Task workflow",
    )

    assert proposal is not None
    assert proposal["valid"] is False
    assert "must originate from a task_workflow Plan" in (
        proposal["validation_error"]
    )


def test_same_semantic_proposal_has_one_cross_surface_identity():
    answer = (
        '```json\n{"action_proposal":{"action":"update-task",'
        '"payload":{"task_id":"TASK-1","priority":1},"reason":"raise"}}\n```'
    )
    web = extract_action_proposal(
        answer,
        user_message="update the task",
        proposal_context={
            "project_id": "web-project",
            "conversation_id": "web-conversation",
            "thread_id": "web-thread",
            "run_id": "web-run",
        },
    )
    feishu = extract_action_proposal(
        answer,
        user_message="update the task",
        proposal_context={
            "conversation_id": "feishu-chat",
            "thread_id": "feishu-thread",
        },
    )

    assert web is not None and feishu is not None
    assert web["proposal_id"] == feishu["proposal_id"]
    assert web["proposal_digest"] == feishu["proposal_digest"]
    events = [
        ZfEvent(
            id="evt-web",
            type="kanban.agent.action.proposed",
            actor="web",
            payload={"proposal": web, "source": "web"},
        ),
        ZfEvent(
            id="evt-feishu",
            type="kanban.agent.action.proposed",
            actor="feishu",
            payload={"proposal": feishu, "source": "feishu"},
        ),
    ]
    items = pending_kanban_proposals(events)
    assert len(items) == 1
    assert set(items[0]["proposal_event_ids"]) == {"evt-web", "evt-feishu"}


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


def test_dismiss_generic_proposal_emits_generic_resolution(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    proposal = _proposed("evt-generic", "通用 workflow proposal")
    proposal.type = "operator.action.proposed"
    proposal.payload["proposal"].update({
        "proposal_id": "proposal-generic",
        "proposal_digest": "a" * 64,
        "revision": 2,
    })
    proposal.task_id = "TASK-GENERIC"
    proposal.payload["proposal"]["payload"]["task_id"] = "TASK-GENERIC"
    log.append(proposal)
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        actor="operator",
        source="cli",
        surface="cli",
    )
    requested = writer.emit(
        "control.action.requested",
        actor="operator",
        payload={},
    )

    result = service.execute(
        action="kanban-proposal-dismiss",
        requested_action="kanban-proposal-dismiss",
        payload={"proposal_event_id": "evt-generic"},
        requested=requested,
    )

    assert result["ok"] is True
    resolved = [
        event
        for event in log.read_all()
        if event.type == "operator.action.resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0].payload["proposal_id"] == "proposal-generic"
    assert resolved[0].payload["revision"] == 2
    assert resolved[0].task_id == "TASK-GENERIC"
    assert result["task_id"] == "TASK-GENERIC"
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


def test_research_adoption_proposal_preserves_semantic_request_id_through_web_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state_dir = _state(tmp_path)
    artifact = state_dir / "research" / "report.md"
    artifact.parent.mkdir()
    artifact.write_text("verified research\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    origin = {
        "schema_version": "workflow-origin-binding.v1",
        "surface": "channel",
        "source": "legacy",
        "project_id": "",
        "channel_id": "ch-research",
        "thread_id": "main",
        "conversation_id": "",
        "thread_key": "",
    }
    (request_dir / "REQ-ADOPT.json").write_text(
        json.dumps({
            "request_id": "REQ-ADOPT",
            "revision": 1,
            "research_artifacts": [],
            "channel_id": "ch-research",
            "thread_id": "main",
            "origin_binding": origin,
        }),
        encoding="utf-8",
    )
    task = bind_workflow_request_to_task(
        mark_workflow_managed_task(
            Task(id="TASK-RESEARCH", title="Research")
        ),
        request_id="REQ-ADOPT",
        request_revision=1,
        origin_binding_digest=workflow_origin_digest(origin),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    terminal_event = ZfEvent(
        id="evt-research-terminal",
        type="fanout.aggregate.completed",
        actor="orchestrator",
        task_id="TASK-RESEARCH",
        payload={
            "fanout_id": "fanout-research",
            "stage_id": "research-fanout",
            "status": "completed",
            "artifact_refs": [{
                "kind": "research_report",
                "ref": "research/report.md",
                "sha256": digest,
                "task_id": "TASK-RESEARCH",
                "workflow_run_id": "wf-research",
                "request_id": "REQ-ADOPT",
                "request_revision": 1,
            }],
        },
    )
    result_event = ZfEvent(
        id="evt-research-result",
        type="workflow.result.available",
        actor="zf-cli",
        task_id="TASK-RESEARCH",
        causation_id=terminal_event.id,
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "request_id": "REQ-ADOPT",
            "request_revision": 1,
            "task_id": "TASK-RESEARCH",
            "workflow_run_id": "wf-research",
            "terminal_event_id": terminal_event.id,
            "artifact_ref": "research/report.md",
            "artifact_digest": digest,
            "summary": "Adopt the verified research.",
            "origin_binding": origin,
        },
    )
    action_payload = {
        "result_event_id": result_event.id,
        "task_id": "TASK-RESEARCH",
        "request_id": "REQ-ADOPT",
        "request_revision": 1,
        "artifact_ref": "research/report.md",
        "artifact_digest": digest,
        "summary": "Adopt the verified research.",
        "channel_id": "ch-research",
        "thread_id": "main",
    }
    event_log = EventLog(state_dir / "events.jsonl")
    event_log.append(terminal_event)
    event_log.append(result_event)
    event_log.append(ZfEvent(
        id="evt-research-adopt",
        type="kanban.agent.action.proposed",
        actor="web",
        payload={
            "source": "web",
            "proposal": {
                "proposal_id": "proposal-research-adopt",
                "proposal_digest": "a" * 64,
                "revision": 1,
                "action": "research-adopt",
                "requested_action": "research-adopt",
                "payload": action_payload,
                "valid": True,
            },
        },
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]

    response = client.post(
        f"/api/projects/{project_id}/actions/research-adopt",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "approve-research-adopt",
        },
        json={
            "project_id": project_id,
            "idempotency_key": "approve-research-adopt",
            "actor": "web",
            "payload": {
                **action_payload,
                "proposal_event_id": "evt-research-adopt",
            },
        },
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "adopted"
    adopted = [
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "workflow.research.adopted"
    ]
    assert len(adopted) == 1
    assert adopted[0].payload["request_id"] == "REQ-ADOPT"


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
    proposed_update = _proposed(
        "evt-upd",
        "改优先级",
        action="update-task",
    )
    proposed_update.payload["proposal"]["payload"] = {
        "task_id": task_id,
        "priority": 1,
    }
    log.append(proposed_update)
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
    assert resolved[0].task_id == task_id
    # the Triage card is now cleared
    assert not any(i["proposal_event_id"] == "evt-upd"
                   for i in pending_kanban_proposals(log.read_all())), "card must clear after execute"

    replay = service.execute(
        action="update-task", requested_action="update-task",
        payload={"task_id": task_id, "priority": 1, "proposal_event_id": "evt-upd"},
        requested=requested,
    )
    assert replay["status"] == "already_resolved"
    assert replay["task_id"] == task_id
    resolved = [
        event
        for event in log.read_all()
        if event.type == "kanban.agent.proposal.resolved"
        and event.payload.get("proposal_event_id") == "evt-upd"
    ]
    assert len(resolved) == 1


def test_approved_proposal_rejects_changed_semantic_payload(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    proposal = _proposed(
        "evt-payload-bound",
        "raise priority",
        action="update-task",
    )
    proposal.payload["proposal"]["payload"] = {
        "task_id": "TASK-X",
        "priority": 1,
    }
    log.append(proposal)
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        actor="operator",
        source="web",
        surface="web",
    )
    requested = writer.emit(
        "web.action.requested",
        actor="operator",
        payload={},
    )

    result = service.execute(
        action="update-task",
        requested_action="update-task",
        payload={
            "task_id": "TASK-X",
            "priority": 2,
            "proposal_event_id": "evt-payload-bound",
        },
        requested=requested,
    )

    assert result["ok"] is False
    assert result["status"] == "proposal_payload_mismatch"
    assert not any(
        event.type in {
            "task.updated",
            "kanban.agent.proposal.resolved",
        }
        for event in log.read_all()
    )


def test_expired_and_superseded_proposals_fail_closed(tmp_path: Path):
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    expired = _proposed("evt-expired", "expired", action="update-task")
    expired.payload["proposal"].update({
        "proposal_id": "proposal-expired",
        "proposal_digest": "a" * 64,
        "expires_at": "2000-01-01T00:00:00Z",
    })
    old = _proposed("evt-old", "old", action="update-task")
    old.payload["proposal"].update({
        "proposal_id": "proposal-revisioned",
        "proposal_digest": "b" * 64,
        "revision": 1,
    })
    new = _proposed("evt-new", "new", action="update-task")
    new.payload["proposal"].update({
        "proposal_id": "proposal-revisioned",
        "proposal_digest": "c" * 64,
        "revision": 2,
    })
    for event in (expired, old, new):
        log.append(event)
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir, writer, actor="operator", source="web", surface="web",
    )
    requested = writer.emit("web.action.requested", actor="operator", payload={})

    expired_result = service.execute(
        action="update-task", requested_action="update-task",
        payload={"task_id": "TASK-X", "proposal_event_id": "evt-expired"},
        requested=requested,
    )
    superseded_result = service.execute(
        action="update-task", requested_action="update-task",
        payload={"task_id": "TASK-X", "proposal_event_id": "evt-old"},
        requested=requested,
    )

    assert expired_result["status"] == "proposal_expired"
    assert superseded_result["status"] == "proposal_superseded"


def test_explicit_supersedes_hides_and_blocks_prior_proposal() -> None:
    prior = _proposed("evt-prior", "Prior")
    prior.payload["proposal"]["proposal_id"] = "proposal-prior"
    replacement = _proposed("evt-replacement", "Replacement")
    replacement.payload["proposal"].update({
        "proposal_id": "proposal-replacement",
        "supersedes": "proposal-prior",
    })
    events = [prior, replacement]

    pending = pending_kanban_proposals(events)
    assert [item["proposal_id"] for item in pending] == [
        "proposal-replacement"
    ]
    gated = proposal_execution_gate(
        events,
        proposal_event_id="evt-prior",
        action="create-task",
    )
    assert gated["status"] == "proposal_superseded"
    assert gated["superseded_by"] == "proposal-replacement"


def test_cross_surface_concurrent_approval_executes_proposal_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    proposal = _proposed("evt-concurrent", "Concurrent task")
    proposal.payload["proposal"].update({
        "proposal_id": "proposal-concurrent",
        "proposal_digest": "d" * 64,
    })
    log.append(proposal)
    setup_writer = EventWriter(log)
    requested = [
        setup_writer.emit(
            "web.action.requested",
            actor=surface,
            payload={"surface": surface},
        )
        for surface in ("web", "feishu")
    ]
    original_create = ControlledActionService._create_task

    def slow_create(self, **kwargs):
        time.sleep(0.1)
        return original_create(self, **kwargs)

    monkeypatch.setattr(
        ControlledActionService,
        "_create_task",
        slow_create,
    )
    start = threading.Barrier(2)

    def approve(index: int) -> dict:
        writer = EventWriter(EventLog(state_dir / "events.jsonl"))
        service = ControlledActionService(
            state_dir,
            writer,
            actor=("web" if index == 0 else "feishu"),
            source=("web" if index == 0 else "feishu"),
            surface=("web" if index == 0 else "feishu"),
        )
        start.wait(timeout=2)
        return service.execute(
            action="create-task",
            requested_action="create-task",
            payload={
                "title": "Concurrent task",
                "proposal_event_id": "evt-concurrent",
            },
            requested=requested[index],
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, range(2)))

    assert sorted(result["status"] for result in results) == [
        "already_resolved",
        "completed",
    ]
    created = [
        event
        for event in log.read_all()
        if event.type == "task.created"
        and event.payload.get("request", {}).get("proposal_event_id")
        == "evt-concurrent"
    ]
    assert len(created) == 1
