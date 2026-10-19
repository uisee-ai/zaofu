"""Owner Other answer -> durable resume -> revised Channel setup confirmation."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.test_kanban_plan_requests import _channel_setup_answer
from tests.test_webchat_runtime_integration import _state, _wait
from zf.core.events import EventLog
from zf.runtime.kanban_plan_requests import plan_response_gate
from zf.web.agent_turn_store import AgentTurnStore
from zf.web.headless_agent import HeadlessTurnResult
from zf.web.server import create_app


@pytest.mark.mock_e2e
def test_other_resumes_exact_plan_and_replaces_it_without_creating_channel(tmp_path, monkeypatch):
    state = _state(tmp_path)
    replies = iter([
        _channel_setup_answer().replace("multi_lens", "clarification"),
        _channel_setup_answer(),
    ])

    def run_turn(**kwargs):
        return HeadlessTurnResult(
            ok=True, status="completed", backend="codex-headless", thread_id="main",
            provider_session_id="other-session", reply=next(replies), messages=[], usage={},
        )

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr("zf.web.observability.create_observed_headless_agent",
                        lambda **kwargs: SimpleNamespace(run_turn=run_turn))
    client = TestClient(create_app(state, project_root=tmp_path))
    headers = {"x-zf-web-token": "test-token"}
    initial = client.post("/api/actions/chat-orchestrator", headers=headers, json={
        "message": "Create a review group", "backend": "codex-headless", "sync": True,
        "project_id": "default", "conversation_id": "kanban:default",
        "thread_key": "main", "turn_id": "other-turn",
    })
    assert initial.status_code == 200, initial.text
    store = AgentTurnStore(state)
    old = store.list_interactions(turn_id="other-turn", status="pending")[0]
    plan = old["request"]
    custom = "使用 multi_lens\n包含产品、架构、critic、UI/UX 和后端视角"
    answer = {"request_event_id": plan["request_event_id"], "request_id": plan["request_id"],
              "revision": plan["revision"], "question_id": plan["question_id"],
              "option_id": "other", "answer": custom}
    gate = plan_response_gate(EventLog(state / "events.jsonl").read_all(), **answer)
    assert gate["ok"], gate
    assert (gate["submit_action"], gate["submit_mode"], gate["submit_payload"]) == ("", "continue", {})
    sent = client.post(f"/api/projects/default/webchat/interactions/{old['interaction_id']}/answer",
                       headers=headers, json={"revision": old["revision"],
                                             "response": {"answer": custom, "plan_response": answer}})
    assert sent.status_code == 200, sent.text
    fresh = _wait(lambda: next((item for item in store.list_interactions(turn_id="other-turn", status="pending")
                               if item["revision"] > old["revision"]), None))
    assert fresh["interaction_id"] == old["interaction_id"]
    revised = fresh["request"]
    assert revised["request_id"] == plan["request_id"]
    assert revised["revision"] == plan["revision"] + 1
    assert revised["options"][0]["submit_payload"]["mode"] == "multi_lens"
    stale = client.post(f"/api/projects/default/webchat/interactions/{old['interaction_id']}/answer",
        headers=headers, json={"revision": old["revision"], "response": {"answer": custom, "plan_response": answer}})
    assert stale.status_code == 409
    events = EventLog(state / "events.jsonl").read_all()
    answered = [event for event in events if event.type == "kanban.agent.plan.answered"]
    assert len(answered) == 1 and answered[0].payload["answer"] == custom
    assert not any(event.type == "channel.created" for event in events)
    # Only the concrete revised selection authorizes creating the group.
    monkeypatch.setattr("zf.runtime.channel_message_ingress.dispatch_channel_replies_background", lambda **kwargs: None)
    approved = client.post("/api/actions/kanban-plan-apply", headers=headers, json={
        "project_id": "default", "conversation_id": "kanban:default", "thread_id": "main",
        "plan_response": {"request_event_id": revised["request_event_id"], "request_id": revised["request_id"],
            "revision": revised["revision"], "question_id": revised["question_id"], "option_id": "quick", "answer": ""},
    })
    assert approved.status_code == 202, approved.text
    assert approved.json()["ok"], approved.text
    from zf.runtime.channel_projection import project_channel
    channel = project_channel(state, approved.json()["channel_id"])
    assert channel["discussion"]["mode"] == "multi_lens"
    closed = client.post(f"/api/projects/default/webchat/interactions/{fresh['interaction_id']}/answer",
        headers=headers, json={"revision": fresh["revision"], "resume": False,
            "response": {"answer": "Quick change", "applied_action": "channel-create-and-start"}})
    assert closed.status_code == 200, closed.text
    assert not store.list_interactions(turn_id="other-turn", status="pending")
    reopened = client.post("/api/actions/chat-orchestrator", headers=headers, json={
        "message": "Continue this applied plan", "backend": "codex-headless",
        "project_id": "default", "conversation_id": "kanban:default", "thread_key": "main",
        "plan_discussion": {key: revised[key] for key in ("request_event_id", "request_id", "revision")},
    })
    assert reopened.status_code == 409
    assert reopened.json()["status"] == "plan_request_already_answered"


@pytest.mark.mock_e2e
@pytest.mark.parametrize("answer_kind", ["other", "continue"])
@pytest.mark.parametrize("scoped", [False, True])
def test_profile_prerequisite_can_continue_the_answered_setup(tmp_path, monkeypatch, answer_kind, scoped):
    from tests.test_channel_profile_registration import setup, payload as profile_payload
    path, writer, service = setup(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "kanban.json").write_text("[]\n")
    custom = "使用 multi_lens 并添加 UI/UX"
    initial_plan = _channel_setup_answer().replace("multi_lens", "clarification")
    if answer_kind == "continue":
        initial_plan = json.dumps({"plan_request": {"subject_type": "clarification",
            "id": "confirm-roster", "question": "Confirm the revised roster?",
            "options": [{"id": "confirm", "label": custom, "description": "Prepare missing profiles"},
                        {"id": "hold", "label": "Hold", "description": "Discuss more"}],
            "allow_other": True}})
    proposal_body = json.dumps({"action_proposal": {"action": "channel-profile-register",
        "payload": profile_payload(path), "intent": {"decision": "propose_action", "source_quote": custom}}})
    replies = iter([initial_plan,
                    "先补齐角色。\n\n```json\n" + proposal_body, _channel_setup_answer()])
    contexts = []

    def run_turn(**kwargs):
        contexts.append(kwargs["context"])
        return HeadlessTurnResult(ok=True, status="completed", backend="codex-headless",
            thread_id="main", provider_session_id="profile-session", reply=next(replies), messages=[], usage={})

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr("zf.web.observability.create_observed_headless_agent",
                        lambda **kwargs: SimpleNamespace(run_turn=run_turn))
    client = TestClient(create_app(state, config=service.config, project_root=tmp_path))
    project_id = client.get("/api/workspace/projects").json()["server_default_project_id"]
    headers = {"x-zf-web-token": "test-token"}
    base = {"backend": "codex-headless", "sync": True, "project_id": project_id,
            "conversation_id": f"kanban:{project_id}", "thread_key": "main"}
    first = client.post("/api/actions/chat-orchestrator", headers=headers,
                        json={**base, "turn_id": "setup-profile", "message": "创建讨论群"})
    assert first.status_code == 200, first.text
    store = AgentTurnStore(state)
    old = store.list_interactions(turn_id="setup-profile", status="pending")[0]
    plan = old["request"]
    answer = {key: plan[key] for key in ("request_event_id", "request_id", "revision", "question_id")}
    answer.update(option_id="other" if answer_kind == "other" else "confirm", answer=custom)
    sent = client.post(f"/api/projects/{project_id}/webchat/interactions/{old['interaction_id']}/answer", headers=headers,
        json={"revision": old["revision"], "response": {"answer": custom, "plan_response": answer}})
    assert sent.status_code == 200, sent.text
    approval = _wait(lambda: next((item for item in store.list_interactions(turn_id="setup-profile", status="pending")
                                  if item["kind"] == "action_approval"), None))
    proposal = approval["request"]
    continuation = proposal["payload"]["plan_continuation"]
    assert continuation["request_event_id"] == plan["request_event_id"]
    applied = client.post("/api/actions/channel-profile-register", headers=headers,
        json={**proposal["payload"], "proposal_event_id": proposal["proposal_event_id"]})
    assert applied.status_code == 200 and applied.json()["ok"], applied.text
    settled = client.post(f"/api/projects/{project_id}/webchat/interactions/{approval['interaction_id']}/answer", headers=headers,
        json={"revision": approval["revision"], "response": {"decision": "approved", "applied_action": "channel-profile-register"}, "resume": False})
    assert settled.status_code == 200, settled.text
    route = f"/api/projects/{project_id}/actions/chat-orchestrator" if scoped else "/api/actions/chat-orchestrator"
    continuation_body = {**base,
        "turn_id": "setup-after-profile", "message": "注册成功，继续原设置并提供确认选项。",
        "plan_discussion": {key: continuation[key] for key in ("request_event_id", "request_id", "revision")}}
    continued = client.post(route, headers=headers, json=(
        {"project_id": project_id, "payload": continuation_body} if scoped else continuation_body))
    assert continued.status_code == 200, continued.text
    assert "ui-ux" in {profile["profile_id"] for profile in contexts[-1]["channel_profile_catalog"]}
    revised = store.list_interactions(turn_id="setup-after-profile", status="pending")[0]["request"]
    assert revised["request_id"] == plan["request_id"] and revised["revision"] == plan["revision"] + 1
    assert revised["valid"]
    assert not store.list_interactions(turn_id="setup-profile", status="pending")
    events = writer.event_log.read_all()
    continued_input = [event for event in events if event.type == "user.message"][-1]
    assert continued_input.payload["request"]["plan_discussion"]["submitted_answers"][0]["answer"] == custom
    assert not any(event.type == "channel.created" for event in events)


@pytest.mark.parametrize("truncated", [False, True])
def test_dedicated_open_action_fence_produces_confirmation_or_explicit_failure(tmp_path, monkeypatch, truncated):
    state = _state(tmp_path)
    body = json.dumps({"action_proposal": {"action": "create-task", "payload": {"title": "Review"},
        "intent": {"decision": "propose_action", "source_quote": "创建任务"}}})
    reply = "准备确认。\n\n```json\n" + (body[:-1] if truncated else body)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr("zf.web.observability.create_observed_headless_agent", lambda **kwargs: SimpleNamespace(
        run_turn=lambda **kwargs: HeadlessTurnResult(ok=True, status="completed", backend="codex-headless",
            thread_id="main", provider_session_id="proposal-session", reply=reply, messages=[], usage={})))
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post("/api/actions/chat-orchestrator", headers={"x-zf-web-token": "test-token"}, json={
        "message": "创建任务", "backend": "codex-headless", "sync": True,
        "project_id": "default", "conversation_id": "kanban:default", "turn_id": "proposal-turn",
    })
    store = AgentTurnStore(state)
    events = EventLog(state / "events.jsonl").read_all()
    if truncated:
        assert response.status_code == 503
        assert "Invalid action proposal" in response.json()["reason"]
        assert store.get("proposal-turn")["state"] == "failed"
        assert not any(event.type == "kanban.agent.action.proposed" for event in events)
    else:
        assert response.status_code == 200, response.text
        assert store.get("proposal-turn")["state"] == "waiting_input"
        assert store.list_interactions(turn_id="proposal-turn", status="pending")[0]["kind"] == "action_approval"
    assert not any(event.type == "task.created" for event in events)
