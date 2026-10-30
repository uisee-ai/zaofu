from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.operator_inbox import build_operator_inbox
from zf.web.projections import read_model


def event(event_type: str, event_id: str, **payload: object) -> ZfEvent:
    return ZfEvent(id=event_id, type=event_type, actor="test", payload=payload)


def test_only_human_decisions_and_legacy_owner_requests_are_projected(tmp_path: Path) -> None:
    events = [
        event("plan.approved", "auto", plan_id="auto-plan", auto=True),
        event("plan.approval.requested", "plan-request", plan_id="plan-1"),
        event("channel.question.opened", "channel", channel_id="c", thread_id="t", question_id="q", kind="owner_decision", question="Which option?"),
        event("channel.question.opened", "agent", channel_id="c", question_id="agent-q", kind="fact", target_member_id="worker", question="What?"),
        event("kanban.agent.plan.requested", "kanban", plan_request={"request_id": "r", "question": "Which route?"}),
        event("human.escalate", "legacy", reason="unknown legacy escalation"),
        event("owner.visible_message.requested", "owner", message_id="m", attention_id="a", human_action_required=True, title="Need owner"),
        event("owner.visible_message.requested", "delivery", message_id="d", delivery_class="run_terminal", human_action_required=True),
        event("approval.requested", "route", approval_ref="owner-visible:m", owner_route="owner_visible_delivery"),
        event("approval.requested", "repair", approval_ref="source-repair:1", owner_route="run_manager",
              approve_action="approve-source-repair-once", reject_action="create-backlog-only"),
    ]
    result = build_operator_inbox(tmp_path, events)
    assert {item["kind"] for item in result["pending"]} == {
        "plan_approval", "channel_decision", "kanban_question", "human_decision", "owner_attention", "approval",
    }
    assert result["summary"]["unread"] == 6
    legacy = next(item for item in result["items"] if item["id"] == "human:legacy")
    assert legacy["verification_required"] is True
    assert legacy["actions"] == []
    assert not any(item.get("approval_ref") == "owner-visible:m" for item in result["items"])
    repair = next(item for item in result["items"] if item.get("approval_ref") == "source-repair:1")
    assert repair["verification_required"] is True
    assert repair["actions"] == []
    assert repair["deep_link"] == "?page=runtime"


def test_versioned_reads_follow_decision_content_and_visible_snapshot(tmp_path: Path) -> None:
    opened = event("channel.question.opened", "open", channel_id="c", question_id="q", kind="owner_decision", question="Choose A", options=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}])
    first = build_operator_inbox(tmp_path, [opened])["items"][0]
    read = event("inbox.item.read", "read", item_id=first["id"], read_version=first["read_version"])
    same = event("channel.question.updated", "same", channel_id="c", question_id="q", question="Choose A")
    assert build_operator_inbox(tmp_path, [opened, read, same])["summary"]["unread"] == 0
    updated = event("channel.question.updated", "update", channel_id="c", question_id="q", options=[{"id": "a", "label": "A"}, {"id": "c", "label": "C"}])
    current = build_operator_inbox(tmp_path, [opened, read, updated])["items"][0]
    assert current["unread"] is True
    assert current["read_version"] != first["read_version"]
    old_all = event("inbox.all.read", "all", items=[{"item_id": first["id"], "read_version": first["read_version"]}])
    later = event("kanban.agent.plan.requested", "new", plan_request={"request_id": "new", "question": "New?"})
    after = build_operator_inbox(tmp_path, [opened, updated, old_all, later])
    assert after["summary"]["unread"] == 2
    legacy_read = event("inbox.item.read", "legacy-read", item_id=first["id"])
    after = build_operator_inbox(tmp_path, [opened, updated, legacy_read, later])
    assert after["summary"]["unread"] == 1
    current_read = event("inbox.item.read", "read-new", item_id=first["id"], read_version=current["read_version"])
    delayed_old = event("inbox.item.read", "read-old-delayed", item_id=first["id"], read_version=first["read_version"])
    after = build_operator_inbox(tmp_path, [opened, updated, current_read, delayed_old])
    assert after["summary"]["unread"] == 0
    reverted = event("channel.question.updated", "reverted", channel_id="c", question_id="q", options=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}])
    assert build_operator_inbox(tmp_path, [opened, updated, current_read, delayed_old, reverted])["summary"]["unread"] == 1


def test_repeated_delivery_of_same_decision_does_not_reopen_unread(tmp_path: Path) -> None:
    first = event("plan.approval.requested", "request-1", plan_id="p")
    item = build_operator_inbox(tmp_path, [first])["pending"][0]
    read = event("inbox.item.read", "read", item_id=item["id"], read_version=item["read_version"])
    duplicate = event("plan.approval.requested", "request-2", plan_id="p")
    result = build_operator_inbox(tmp_path, [first, read, duplicate])
    assert result["summary"]["pending"] == 1
    assert result["summary"]["unread"] == 0
    assert result["pending"][0]["dedupe_count"] == 2


def test_sqlite_and_event_ledger_agree_on_decisions_and_reads(tmp_path: Path) -> None:
    state = tmp_path / ".zf"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    opened = event("channel.question.opened", "open", channel_id="c", thread_id="main", question_id="q", kind="owner_decision", question="Choose")
    log.append(opened)
    log.append(event("kanban.agent.plan.requested", "request", plan_request={"request_id": "r", "question": "Decide"}))
    first = build_operator_inbox(state, log.read_all())
    item = next(item for item in first["items"] if item["kind"] == "channel_decision")
    log.append(event("inbox.item.read", "read", item_id=item["id"], read_version=item["read_version"]))
    expected = build_operator_inbox(state, log.read_all(), project_root=tmp_path)
    projected = read_model.operator_inbox(state, project_root=tmp_path)
    assert projected is not None and projected["source"] == "read_model.sqlite"
    assert projected["items"] == expected["items"]
    assert projected["summary"]["unread"] == 1
    assert projected["summary"]["action_required_pending"] == 2


def test_channel_inbox_obeys_authoritative_question_admission(tmp_path: Path) -> None:
    from tests.test_channel_collaboration_closure import CH, defer, seed

    state, log, writer, service = seed(tmp_path)
    for actor, thread, resolution in (
        ("tech_leader", "main", "deferred"),
        ("web", "foreign", "answered"),
        ("web", "main", "unknown"),
    ):
        writer.emit("channel.question.resolved", actor=actor, payload={
            "channel_id": CH, "thread_id": thread, "question_id": "q-db",
            "resolution": resolution, "resolved_by": "owner:operator",
        })
    writer.emit("channel.question.merged", actor="tech_leader", payload={
        "channel_id": CH, "thread_id": "main", "question_id": "q-db",
        "into_question_id": "missing",
    })
    raw = build_operator_inbox(state, log.read_all(), project_root=tmp_path)
    indexed = read_model.operator_inbox(state, project_root=tmp_path)
    assert raw["items"] == indexed["items"]
    assert raw["pending"][0]["question_id"] == "q-db"
    assert defer(service, writer)["ok"]
    raw = build_operator_inbox(state, log.read_all(), project_root=tmp_path)
    indexed = read_model.operator_inbox(state, project_root=tmp_path)
    assert raw["items"] == indexed["items"]
    assert raw["items"][0]["status"] == "resolved"


def test_channel_question_identity_is_scoped_to_its_channel(tmp_path: Path) -> None:
    events = [
        event("channel.question.opened", "first", channel_id="a", question_id="q", question="First?"),
        event("channel.question.opened", "second", channel_id="b", question_id="q", question="Second?"),
        event("channel.question.resolved", "answer", channel_id="b", question_id="q", resolution="answered"),
    ]
    items = {item["channel_id"]: item for item in build_operator_inbox(tmp_path, events)["items"]}
    assert items["a"]["status"] == "pending"
    assert items["b"]["status"] == "resolved"


def test_plan_decision_requires_current_request_and_serializes_competing_actions(tmp_path: Path) -> None:
    state = tmp_path / ".zf"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    log.append(event("plan.approval.requested", "request-1", plan_id="p"))

    def decide(action: str, expected: str) -> dict:
        service = ControlledActionService(state, EventWriter(EventLog(state / "events.jsonl")), config=ZfConfig(project=ProjectConfig(name="test")))
        return service._execute_action(requested=event("control.action.requested", action), action=action, requested_action=action,
            payload={"plan_id": "p", "request_event_id": expected, "reason": "reconsider"})

    assert decide("plan-approve", "stale")["status"] == "stale_decision"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda action: decide(action, "request-1"), ("plan-approve", "plan-reject")))
    assert sum(bool(result["ok"]) for result in results) == 1
    assert len([e for e in log.read_all() if e.type in {"plan.approved", "plan.rejected"}]) == 1


def test_semantic_adoption_does_not_replace_owner_plan_approval(tmp_path: Path) -> None:
    from zf.runtime.operator_plan_preview import build_plan_preview

    state = tmp_path / ".zf"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    log.append(event("plan.approval.requested", "request", plan_id="plan-1"))
    log.append(event("plan.approved", "semantic", plan_id="plan-1", semantic_control=True))
    assert build_operator_inbox(state, log.read_all())["summary"]["pending"] == 1
    preview = build_plan_preview(state, log.read_all(), plan_id="plan-1", project_root=tmp_path)
    assert preview["status"] == "pending"
    assert preview["requested_event_id"] == "request"
    service = ControlledActionService(state, EventWriter(log), config=ZfConfig(project=ProjectConfig(name="test")))
    result = service._execute_action(
        requested=event("control.action.requested", "action"), action="plan-approve", requested_action="plan-approve",
        payload={"plan_id": "plan-1", "request_event_id": "request"},
    )
    assert result["ok"] is True
    assert build_operator_inbox(state, log.read_all())["items"][0]["status"] == "approved"
    auto_events = [event("plan.approval.requested", "old", plan_id="p"),
                   event("plan.approved", "automatic", plan_id="p", auto=True)]
    assert build_operator_inbox(state, auto_events)["items"] == []


def test_inbox_replan_rejects_stale_and_competing_decisions(tmp_path: Path) -> None:
    state = tmp_path / ".zf"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    body = {
        "approval_ref": "approval-1", "owner_route": "plan_synth_owner_decision",
        "checkpoint_id": "checkpoint-1", "workflow_run_id": "run-1",
        "fanout_id": "fanout-1", "source_event_id": "source-1",
        "proposal_ref": "proposal.json", "eval_ref": "eval.json",
        "candidate_task_map_ref": "task-map.json",
    }
    log.append(event("approval.requested", "approval-event", **body))

    def decide(action: str, source: str = "operator-inbox", **changes: str) -> dict:
        service = ControlledActionService(
            state, EventWriter(EventLog(state / "events.jsonl")),
            config=ZfConfig(project=ProjectConfig(name="test")), source=source,
        )
        return service._execute_action(
            requested=event("control.action.requested", action), action=action, requested_action=action,
            payload={**body, "source": source, "created_event_id": "approval-event", **changes},
        )

    assert decide("replan-approve", checkpoint_id="old")["status"] == "stale_decision"
    assert decide("replan-approve", created_event_id="old")["status"] == "stale_decision"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda choice: decide(*choice),
                                (("replan-approve", "operator-inbox"), ("replan-reject", "kanban-agent"))))
    assert sum(bool(result["ok"]) for result in results) == 1
    assert len([e for e in log.read_all() if e.type.startswith("replan.owner_decision.")]) == 1
    assert len([e for e in log.read_all() if e.type == "approval.resolved"]) == 1


def test_real_web_read_action_updates_sqlite_and_keeps_decision_pending(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    log.append(event("channel.question.opened", "open", channel_id="c", thread_id="main", question_id="q", kind="owner_decision", question="Decide"))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/snapshot").json()["project"]["project_id"]
    path = f"/api/projects/{project}/operator/inbox"
    before = client.get(path).json()
    item = before["pending"][0]
    result = client.post(f"/api/projects/{project}/actions/inbox-item-read", headers={"x-zf-web-token": "test-token"},
        json={"project_id": project, "idempotency_key": "read-1", "payload": {"item_id": item["id"], "read_version": item["read_version"]}})
    assert result.status_code == 202
    after = client.get(path).json()
    assert after["source"] == "read_model.sqlite"
    assert after["summary"]["unread"] == 0
    assert after["summary"]["action_required_pending"] == 1
    assert after["items"][0]["unread"] is False


def test_human_decision_submission_is_visible_and_cannot_be_submitted_twice(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    log.append(event("human.escalate", "escalation", decision_token="hdec-1", checkpoint_id="ck", reason="Needs decision"))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/snapshot").json()["project"]["project_id"]
    inbox_path = f"/api/projects/{project}/operator/inbox"
    action_path = f"/api/projects/{project}/actions/human-decision-request-autoresearch"
    payload = {"decision_token": "hdec-1", "checkpoint_id": "ck", "created_event_id": "escalation"}
    def submit(key: str):
        return client.post(action_path, headers={"x-zf-web-token": "test-token"},
                           json={"project_id": project, "idempotency_key": key, "payload": payload})
    assert submit("first").status_code == 202
    inbox = client.get(inbox_path).json()
    assert inbox["items"][0]["status"] == "processing"
    assert inbox["summary"]["pending"] == 0
    assert submit("second").status_code == 409
    log.append(event("run.manager.human_decision.applied", "applied", decision_token="hdec-1", decision="request_autoresearch", autoresearch_requested=False, reason="request failed"))
    inbox = client.get(inbox_path).json()
    assert inbox["items"][0]["status"] == "failed"
    assert inbox["items"][0]["resolution"] == "request failed"


def test_human_decision_requires_contract_and_current_run_state(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    log.append(event("human.escalate", "legacy", decision_token="legacy-token", reason="missing contract"))
    log.append(event("human.escalate", "current", decision_token="current-token", checkpoint_id="ck", run_id="run-1"))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/snapshot").json()["project"]["project_id"]
    action_path = f"/api/projects/{project}/actions/human-decision-request-autoresearch"
    def submit(token: str):
        return client.post(action_path, headers={"x-zf-web-token": "test-token"},
                           json={"project_id": project, "idempotency_key": token,
                                 "payload": {"decision_token": token}})
    assert submit("legacy-token").status_code == 409
    log.append(event("verify.passed", "progress", run_id="run-1"))
    assert submit("current-token").status_code == 409
    raw = build_operator_inbox(state, log.read_all(), project_root=tmp_path)
    indexed = read_model.operator_inbox(state, project_root=tmp_path)
    assert raw["items"] == indexed["items"]
    by_token = {row["decision_token"]: row for row in raw["items"]}
    assert by_token["legacy-token"]["verification_required"] is True
    assert by_token["legacy-token"]["actions"] == []
    assert by_token["current-token"]["status"] == "superseded"
    assert not any(e.type == "human.escalation.acknowledged" for e in log.read_all())


def test_unscoped_legacy_escalation_is_not_hidden_by_other_run_progress(tmp_path: Path) -> None:
    state = tmp_path / ".zf"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    log.append(event("human.escalate", "legacy", decision_token="old", checkpoint_id="ck", reason="repair required"))
    log.append(event("verify.passed", "other-run-progress", run_id="other-run"))
    raw = build_operator_inbox(state, log.read_all(), project_root=tmp_path)
    indexed = read_model.operator_inbox(state, project_root=tmp_path)
    assert raw["items"] == indexed["items"]
    assert raw["pending"][0]["verification_required"] is True
    assert raw["pending"][0]["actions"] == []


def test_owner_delivery_failures_group_by_route_and_show_recovery() -> None:
    from zf.runtime.owner_delivery_health import owner_delivery_health

    events = [
        event("owner.visible_message.failed", "a", target="feishu", route="approval", reason="route missing"),
        event("owner.visible_message.failed", "b", target="feishu", route="approval", reason="route missing"),
        event("owner.visible_message.failed", "c", target="feishu", route="alert", reason="timeout"),
        event("owner.visible_message.delivered", "d", target="feishu", route="approval"),
    ]
    routes = {row["route"]: row for row in owner_delivery_health(events)}
    assert routes["approval"]["failure_count"] == 2
    assert routes["approval"]["status"] == "delivered"
    assert routes["alert"]["status"] == "failed"
