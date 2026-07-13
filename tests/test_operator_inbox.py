from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.operator_inbox import build_operator_inbox
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.operator_intent import infer_operator_intent


def _state(tmp_path: Path) -> tuple[Path, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return state_dir, EventLog(state_dir / "events.jsonl")


def _write_plan_artifacts(state_dir: Path) -> tuple[str, str]:
    digest = state_dir / "artifacts" / "plan-digest" / "evt-plan-1.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("# Plan Ready\n\nReview this plan.\n", encoding="utf-8")
    task_map = state_dir / "artifacts" / "task-map.json"
    task_map.write_text(
        json.dumps({
            "tasks": [{
                "task_id": "TASK-1",
                "title": "Implement card",
                "root_owner_class": "assembly",
                "verification": "pytest",
            }],
        }),
        encoding="utf-8",
    )
    return "artifacts/plan-digest/evt-plan-1.md", str(task_map)


def _request_plan(log: EventLog, digest_ref: str, task_map_ref: str) -> None:
    log.append(ZfEvent(
        id="evt-request",
        type="plan.approval.requested",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "plan_id": "evt-plan-1",
            "stage_id": "writer",
            "trace_id": "trace-1",
            "pdd_id": "PDD-1",
            "task_count": 1,
            "digest_ref": digest_ref,
            "task_map_ref": task_map_ref,
        },
    ))


def test_operator_inbox_projects_pending_and_resolved_plan(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    digest_ref, task_map_ref = _write_plan_artifacts(state_dir)
    _request_plan(log, digest_ref, task_map_ref)

    inbox = build_operator_inbox(state_dir, log.read_all(), project_root=tmp_path)
    item = inbox["pending"][0]

    assert inbox["schema_version"] == "operator-inbox.v2"
    assert item["kind"] == "plan_approval"
    assert item["plan_id"] == "evt-plan-1"
    assert item["preview"]["available"] is True
    assert item["policy"]["agent_can_propose_plan_approve"] is False

    log.append(ZfEvent(
        type="plan.rejected",
        actor="operator",
        payload={"plan_id": "evt-plan-1", "reason": "missing assembly detail"},
    ))
    resolved = build_operator_inbox(state_dir, log.read_all(), project_root=tmp_path)

    assert resolved["summary"]["pending"] == 0
    assert resolved["items"][0]["status"] == "rejected"
    assert resolved["items"][0]["reject_reason"] == "missing assembly detail"


def test_plan_preview_reads_digest_and_task_map(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    digest_ref, task_map_ref = _write_plan_artifacts(state_dir)
    _request_plan(log, digest_ref, task_map_ref)

    preview = build_plan_preview(
        state_dir,
        log.read_all(),
        plan_id="evt-plan-1",
        project_root=tmp_path,
    )

    assert preview["ok"] is True
    assert preview["status"] == "pending"
    assert preview["refs"]["markdown_ref"] == digest_ref
    assert "# Plan Ready" in preview["markdown"]
    assert preview["task_map_summary"]["task_count"] == 1
    assert preview["actions"]["repair_chat"] == "chat-orchestrator"


def test_kanban_agent_cannot_propose_plan_approve() -> None:
    intent = infer_operator_intent(
        "approve this plan",
        payload={"action": "plan-approve"},
        project_id="demo",
    )

    assert "plan-approve" not in intent["proposed_actions"]
    assert intent["blocked_actions"] == ["plan-approve"]


def test_operator_inbox_web_api_and_action_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state_dir, log = _state(tmp_path)
    digest_ref, task_map_ref = _write_plan_artifacts(state_dir)
    _request_plan(log, digest_ref, task_map_ref)
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]

    inbox = client.get(f"/api/projects/{project_id}/operator/inbox")
    assert inbox.status_code == 200
    assert inbox.json()["summary"]["pending"] == 1

    preview = client.get(f"/api/projects/{project_id}/plans/evt-plan-1/preview")
    assert preview.status_code == 200
    assert preview.json()["markdown"].startswith("# Plan Ready")

    no_token = client.post(
        f"/api/projects/{project_id}/actions/plan-approve",
        json={
            "project_id": project_id,
            "idempotency_key": "plan-approve:no-token",
            "payload": {"plan_id": "evt-plan-1"},
        },
    )
    assert no_token.status_code == 403

    wrong_token = client.post(
        f"/api/projects/{project_id}/actions/plan-approve",
        headers={"x-zf-web-token": "wrong"},
        json={
            "project_id": project_id,
            "idempotency_key": "plan-approve:wrong-token",
            "payload": {"plan_id": "evt-plan-1"},
        },
    )
    assert wrong_token.status_code == 403

    approved = client.post(
        f"/api/projects/{project_id}/actions/plan-approve",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_id,
            "idempotency_key": "plan-approve:ok",
            "payload": {"plan_id": "evt-plan-1"},
        },
    )
    assert approved.status_code == 200
    assert approved.json()["action"] == "plan-approve"

    after = client.get(f"/api/projects/{project_id}/operator/inbox").json()
    assert after["summary"]["pending"] == 0
    assert any(event.type == "plan.approved" for event in log.read_all())


def test_operator_inbox_web_api_projects_run_manager_human_decision(
    tmp_path: Path,
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    state_dir, log = _state(tmp_path)
    log.append(ZfEvent(
        type="human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "hdec-web-1",
            "checkpoint_id": "ck-web-1",
            "fingerprint": "fp-web-1",
            "reason": "candidate rework exhausted",
        },
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]

    inbox = client.get(f"/api/projects/{project_id}/operator/inbox")

    assert inbox.status_code == 200
    body = inbox.json()
    assert body["source"] == "read_model.sqlite"
    assert body["summary"]["pending"] == 1
    assert body["summary"]["human_decisions"] == 1
    assert body["pending"][0]["kind"] == "human_decision"
    assert body["pending"][0]["decision_token"] == "hdec-web-1"


def test_operator_inbox_projects_run_manager_human_decision(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    log.append(ZfEvent(
        type="human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "hdec-123",
            "checkpoint_id": "ck-1",
            "fingerprint": "fp-1",
            "reason": "trigger_rework requires owner approval",
        },
    ))

    pending = build_operator_inbox(state_dir, log.read_all())
    item = pending["pending"][0]
    assert item["kind"] == "human_decision"
    assert item["decision_token"] == "hdec-123"
    assert pending["summary"]["human_decisions"] == 1

    log.append(ZfEvent(
        type="run.manager.human_decision.applied",
        actor="run-manager",
        payload={
            "decision_token": "hdec-123",
            "decision": "approve_controlled_action",
            "next_route": "controlled_action",
        },
    ))
    resolved = build_operator_inbox(state_dir, log.read_all())
    assert resolved["summary"]["pending"] == 0
    assert resolved["items"][0]["status"] == "applied"


def test_operator_inbox_excludes_automation_attention(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    log.append(ZfEvent(
        type="runtime.attention.needed",
        actor="supervisor",
        payload={
            "attention_id": "attn-1",
            "fingerprint": "fanout:child:pending",
            "title": "Fanout child pending",
            "summary": "child still has no terminal event",
        },
    ))

    pending = build_operator_inbox(state_dir, log.read_all())
    assert pending["summary"]["pending"] == 0
    assert pending["summary"]["unread"] == 0

    log.append(ZfEvent(
        type="runtime.attention.acknowledged",
        actor="web",
        payload={
            "fingerprint": "fanout:child:pending",
            "reason": "operator reviewed",
        },
    ))

    acknowledged = build_operator_inbox(state_dir, log.read_all())
    assert acknowledged["summary"]["pending"] == 0
    assert acknowledged["summary"]["attention"] == 0
    assert acknowledged["summary"]["suppressed_acknowledged"] == 1
    assert acknowledged["items"] == []


def test_operator_inbox_classifies_and_dedupes_runtime_noise(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    for index in range(30):
        log.append(ZfEvent(
            id=f"evt-attn-{index}",
            type="runtime.attention.needed",
            actor="supervisor",
            payload={
                "attention_id": f"attn-{index}",
                "fingerprint": "fanout:child:pending",
                "title": "Fanout child dispatched without a terminal child event",
                "summary": "Fanout child dispatched without a terminal child event",
            },
        ))
    log.append(ZfEvent(
        id="evt-human-1",
        type="human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "hdec-runtime-1",
            "checkpoint_id": "ck-runtime-1",
            "fingerprint": "trigger-rework:broad-scope",
            "reason": "trigger_rework mutates broad candidate scope and requires owner approval",
        },
    ))

    inbox = build_operator_inbox(state_dir, log.read_all())

    assert inbox["summary"]["pending"] == 1
    assert inbox["summary"]["action_required_pending"] == 1
    assert inbox["summary"]["noise_pending"] == 0
    assert inbox["views"]["action_required"]["count"] == 1
    assert inbox["views"]["automation"]["count"] == 0

    human_item = next(item for item in inbox["items"] if item["kind"] == "human_decision")
    assert human_item["category"] == "action_required"
    assert human_item["actionability"] == "human_required"
    assert human_item["owner_route"] == "human"



def test_operator_inbox_tracks_read_state_without_removing_history(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    log.append(ZfEvent(id="evt-human", type="human.escalate", actor="run-manager", payload={
        "decision_token": "hdec-read", "reason": "needs operator decision",
    }))
    unread = build_operator_inbox(state_dir, log.read_all())
    assert unread["summary"]["unread"] == 1
    item_id = unread["items"][0]["id"]

    log.append(ZfEvent(type="inbox.item.read", actor="web", payload={"item_id": item_id}))
    read = build_operator_inbox(state_dir, log.read_all())
    assert read["summary"]["unread"] == 0
    assert read["items"][0]["unread"] is False
    assert read["items"][0]["status"] == "pending"


def test_operator_inbox_does_not_create_ack_only_items(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    log.append(ZfEvent(
        type="runtime.attention.acknowledged",
        actor="web",
        payload={
            "attention_id": "attn-ack-only",
            "fingerprint": "ack-only",
            "reason": "operator reviewed",
        },
    ))
    log.append(ZfEvent(
        type="human.escalation.acknowledged",
        actor="web",
        payload={
            "decision_token": "hdec-ack-only",
            "decision": "request_autoresearch",
        },
    ))

    inbox = build_operator_inbox(state_dir, log.read_all())
    assert inbox["summary"]["pending"] == 0
    assert inbox["summary"]["total"] == 0
    assert inbox["summary"]["suppressed_acknowledged"] == 2
    assert inbox["items"] == []


def test_operator_inbox_read_model_includes_human_decision(tmp_path: Path) -> None:
    from zf.web.projections import read_model

    state_dir, log = _state(tmp_path)
    log.append(ZfEvent(
        type="human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "hdec-read-model",
            "reason": "needs owner decision",
        },
    ))

    projected = read_model.operator_inbox(state_dir, project_root=tmp_path)
    assert projected is not None
    assert projected["source"] == "read_model.sqlite"
    assert projected["summary"]["human_decisions"] == 1
    assert projected["pending"][0]["kind"] == "human_decision"
    assert projected["pending"][0]["decision_token"] == "hdec-read-model"


def test_operator_inbox_read_model_preserves_plan_preview_refs(tmp_path: Path) -> None:
    from zf.web.projections import read_model

    state_dir, log = _state(tmp_path)
    digest_ref, task_map_ref = _write_plan_artifacts(state_dir)
    _request_plan(log, digest_ref, task_map_ref)

    projected = read_model.operator_inbox(state_dir, project_root=tmp_path)

    assert projected is not None
    item = projected["pending"][0]
    assert item["kind"] == "plan_approval"
    assert item["stage_id"] == "writer"
    assert item["trace_id"] == "trace-1"
    assert item["task_count"] == 1
    assert item["refs"]["digest_ref"] == digest_ref
    assert item["refs"]["task_map_ref"] == task_map_ref
    assert item["preview"]["available"] is True


def test_operator_inbox_web_human_decision_action_acknowledges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state_dir, log = _state(tmp_path)
    log.append(ZfEvent(
        type="human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "hdec-web-1",
            "checkpoint_id": "ck-web",
            "fingerprint": "fp-web",
            "reason": "trigger_rework requires owner approval",
        },
    ))
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]

    missing_token = client.post(
        f"/api/projects/{project_id}/actions/human-decision-request-autoresearch",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_id,
            "idempotency_key": "human-decision:missing-token",
            "payload": {},
        },
    )
    assert missing_token.status_code == 422
    assert missing_token.json()["reason"] == "decision_token is required"

    response = client.post(
        f"/api/projects/{project_id}/actions/human-decision-request-autoresearch",
        headers={"x-zf-web-token": "test-token"},
        json={
            "project_id": project_id,
            "idempotency_key": "human-decision:diagnose",
            "payload": {
                "decision_token": "hdec-web-1",
                "checkpoint_id": "ck-web",
                "fingerprint": "fp-web",
                "source": "operator-inbox",
            },
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "acknowledged"
    assert response.json()["decision"] == "request_autoresearch"

    events = log.read_all()
    acknowledged = [event for event in events if event.type == "human.escalation.acknowledged"]
    assert acknowledged
    assert acknowledged[-1].payload["decision_token"] == "hdec-web-1"
    assert acknowledged[-1].payload["decision"] == "request_autoresearch"

    after = client.get(f"/api/projects/{project_id}/operator/inbox").json()
    assert after["summary"]["pending"] == 0
    assert after["summary"]["suppressed_acknowledged"] == 1
    assert after["items"] == []
