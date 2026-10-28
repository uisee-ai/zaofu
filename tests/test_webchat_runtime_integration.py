from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events import EventLog, ZfEvent
from zf.web.agent_artifact_store import AgentArtifactStore
from zf.web.agent_conversation_store import AgentConversationStore
from zf.web.agent_turn_store import AgentTurnStore
from zf.web.headless_agent import HeadlessMessage
from zf.web.agent_runtime_db import connect
from zf.web.server import create_app
from zf.web.webchat_runtime import (
    DurablePartRecorder,
    prepare_durable_webchat_run,
    settle_durable_reply,
)


def _state(tmp_path: Path) -> Path:
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]", encoding="utf-8")
    (state / "feature_list.json").write_text("[]", encoding="utf-8")
    EventLog(state / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
    return state


def _wait(predicate, *, timeout: float = 5.0):  # noqa: ANN001, ANN202
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("condition did not become true")


def _fake_codex_native_question_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_codex_native_question.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "for line in sys.stdin:",
            "    if not line.strip():",
            "        continue",
            "    message = json.loads(line)",
            "    method = message.get('method')",
            "    request_id = message.get('id')",
            "    if request_id == 99 and not method:",
            "        assert message.get('error', {}).get('code') == -32001, message",
            "    elif request_id and method == 'initialize':",
            "        print(json.dumps({'jsonrpc':'2.0','id':request_id,'result':{'serverInfo':{'name':'fake-codex'}}}), flush=True)",
            "    elif method == 'initialized':",
            "        pass",
            "    elif request_id and method == 'thread/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':request_id,'result':{'threadId':'native-question-session'}}), flush=True)",
            "    elif request_id and method == 'turn/start':",
            "        print(json.dumps({'jsonrpc':'2.0','id':request_id,'result':{'turnId':'provider-turn-1'}}), flush=True)",
            "        question = {'id':'route','header':'Route','question':'Which route?','options':[{'id':'a','label':'A'},{'id':'b','label':'B'}]}",
            "        params = {'threadId':'native-question-session','turnId':'provider-turn-1','itemId':'question-1','questions':[question]}",
            "        print(json.dumps({'jsonrpc':'2.0','id':99,'method':'item/tool/requestUserInput','params':params}), flush=True)",
        ]),
        encoding="utf-8",
    )
    return script


def test_durable_turn_keeps_source_context_and_observed_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from zf.web.headless_agent import HeadlessTurnResult

    state = _state(tmp_path)
    captured = {}
    call_settings = {}

    def run_turn(**kwargs):
        captured.update(kwargs["context"])
        call_settings.update({"model": kwargs.get("model"), "thinking_level": kwargs.get("thinking_level")})
        return HeadlessTurnResult(
            ok=True, status="completed", backend="codex-headless",
            thread_id="main", provider_session_id="merge-session",
            reply="context preserved", messages=[], usage={},
        )

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr(
        "zf.web.observability.create_observed_headless_agent",
        lambda **kwargs: SimpleNamespace(run_turn=run_turn),
    )
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "codex-headless", "sync": True,
            "project_id": "default", "conversation_id": "conversation-a",
            "thread_key": "main", "turn_id": "turn-merged",
            "message": "Keep the owner requirement attached to this turn",
            "model": "example-model", "thinking_level": "high",
            "attachment_refs": [{"version_id": "merge-attachment", "digest": "merge-digest"}],
        },
    )
    assert response.status_code == 200
    assert captured["process_run_id"]
    assert captured["requirement_source_messages"][-1]["message"] == "Keep the owner requirement attached to this turn"
    assert captured["attachment_refs"][0]["version_id"] == "merge-attachment"
    assert "conversation_summary" in captured and "agent_profile" in captured
    assert call_settings == {"model": "example-model", "thinking_level": "high"}
    assert AgentTurnStore(state).get("turn-merged")["state"] == "completed"
    stored = AgentTurnStore(state).get("turn-merged")["request"]["payload"]
    assert stored["model"] == "example-model" and stored["thinking_level"] == "high"
    created = EventLog(state / "events.jsonl").query(event_type="kanban.agent.turn.created")[-1]
    assert created.payload["requested_model"] == "example-model"
    assert created.payload["requested_thinking_level"] == "high"
    completed = EventLog(state / "events.jsonl").query(event_type="kanban.agent.turn.completed")[-1]
    assert completed.payload["status"] == completed.payload["logical_state"] == "completed"
    assert completed.payload["outcome"] == "completed"


def test_effort_only_reaches_codex_and_durable_request_without_changing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from zf.web.headless_agent import HeadlessTurnResult

    state = _state(tmp_path)
    calls = []
    def run_turn(**kwargs):
        calls.append((kwargs.get("model"), kwargs.get("thinking_level")))
        return HeadlessTurnResult(ok=True, status="completed", backend="codex-headless", thread_id="main",
                                  provider_session_id="session", reply="ok", messages=[], usage={})
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr("zf.web.observability.create_observed_headless_agent", lambda **kwargs: SimpleNamespace(run_turn=run_turn))
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post("/api/actions/chat-orchestrator", headers={"x-zf-web-token": "test-token"}, json={
        "backend": "codex-headless", "sync": True, "project_id": "default", "conversation_id": "c",
        "thread_key": "main", "turn_id": "effort-only", "message": "check", "thinking_level": "high",
    })
    assert response.status_code == 200
    assert calls == [(None, "high")]
    payload = AgentTurnStore(state).get("effort-only")["request"]["payload"]
    assert payload.get("thinking_level") == "high" and not payload.get("model")


def test_durable_provider_exception_keeps_failure_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    state = _state(tmp_path)

    def run_turn(**kwargs):
        raise RuntimeError("Codex provider authentication failure")

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr(
        "zf.web.observability.create_observed_headless_agent",
        lambda **kwargs: SimpleNamespace(run_turn=run_turn),
    )
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "codex-headless", "sync": True,
            "project_id": "default", "conversation_id": "conversation-a",
            "thread_key": "main", "turn_id": "turn-merged-failure", "message": "test failure",
        },
    )
    assert not response.json()["ok"]
    assert AgentTurnStore(state).get("turn-merged-failure")["state"] == "failed"
    failure = EventLog(state / "events.jsonl").query(event_type="kanban.agent.turn.failed")[-1]
    assert failure.payload["outcome"] == "failed"
    assert failure.payload["failure_class"] == "provider_error"


def test_queued_turn_preserves_model_and_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from zf.web.headless_agent import HeadlessTurnResult

    state = _state(tmp_path)
    settings: list[tuple[str, str]] = []

    def run_turn(**kwargs):
        settings.append((str(kwargs.get("model") or ""), str(kwargs.get("thinking_level") or "")))
        return HeadlessTurnResult(ok=True, status="completed", backend="codex-headless", thread_id="main",
                                  provider_session_id="session", reply="done", messages=[], usage={})

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr("zf.web.observability.create_observed_headless_agent", lambda **kwargs: SimpleNamespace(run_turn=run_turn))
    store = AgentTurnStore(state)
    store.enqueue(project_id="default", conversation_id="conversation-a", thread_id="main",
                  message="follow-up", idempotency_key="queued-model", source_turn_id="first",
                  request={"backend": "codex-headless", "model": "model-a", "thinking_level": "high"})
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post("/api/actions/chat-orchestrator", headers={"x-zf-web-token": "test-token"}, json={
        "backend": "codex-headless", "sync": True, "project_id": "default",
        "conversation_id": "conversation-a", "thread_key": "main", "turn_id": "first", "message": "start",
    })
    assert response.status_code == 200
    _wait(lambda: ("model-a", "high") in settings)


def test_terminal_turn_claims_and_dispatches_one_durable_queue_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    script = tmp_path / "fake_queue_provider.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'queue-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'queue-session','result':'done'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    store = AgentTurnStore(state)
    store.enqueue(
        project_id="default", conversation_id="conversation-a", thread_id="main",
        message="queued follow-up", idempotency_key="queue-follow-up",
        request={"backend": "claude-headless", "permission_profile": "read_only"},
        source_turn_id="turn-first",
    )
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless", "sync": True,
            "project_id": "default", "conversation_id": "conversation-a",
            "thread_key": "main", "turn_id": "turn-first", "message": "first",
        },
    )
    assert response.status_code == 200
    assert response.json()["queue_dispatch"]["queue_id"]
    _wait(lambda: len(store.list_turns(project_id="default")) == 2)
    queue = store.list_queue(project_id="default", include_terminal=True)
    assert len(queue) == 1
    assert queue[0]["state"] == "completed"
    assert len({item["turn_id"] for item in store.list_turns(project_id="default")}) == 2


def test_queue_posted_after_source_turn_terminal_dispatches_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    script = tmp_path / "fake_late_queue_provider.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'late-queue-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'late-queue-session','result':'done'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state, project_root=tmp_path))
    first = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless", "sync": True,
            "project_id": "default", "conversation_id": "conversation-a",
            "thread_key": "main", "turn_id": "turn-terminal", "message": "first",
        },
    )
    assert first.status_code == 200
    store = AgentTurnStore(state)
    assert store.get("turn-terminal")["state"] == "completed"

    queued = client.post(
        "/api/projects/default/webchat/queue",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless",
            "permission_profile": "read_only",
            "conversation_id": "conversation-a",
            "thread_id": "main",
            "message": "late queued follow-up",
            "idempotency_key": "late-queue-follow-up",
            "source_turn_id": "turn-terminal",
        },
    )
    assert queued.status_code == 201
    assert queued.json()["queue_dispatch"]["queue_id"]
    _wait(lambda: len(store.list_turns(project_id="default")) == 2)
    _wait(lambda: store.list_queue(
        project_id="default", include_terminal=True,
    )[0]["state"] == "completed")


def test_durable_question_answer_starts_new_attempt_on_same_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    script = tmp_path / "fake_question_provider.py"
    plan = {
        "plan_request": {
            "header": "Scope", "id": "scope", "question": "Choose scope",
            "options": [
                {"id": "small", "label": "Small (Recommended)", "description": "Small scope"},
                {"id": "large", "label": "Large", "description": "Large scope"},
            ],
        }
    }
    script.write_text(
        "\n".join([
            "import json, sys",
            "line = sys.stdin.readline()",
            f"plan = {plan!r}",
            "prompt = json.loads(line).get('prompt', line) if line.strip().startswith('{') else line",
            "result = 'implemented after owner answer' if 'owner answer' in prompt else json.dumps(plan)",
            "print(json.dumps({'type':'system','session_id':'question-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'question-session','result':result}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state, project_root=tmp_path))
    first = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless", "sync": True,
            "project_id": "default", "conversation_id": "conversation-a",
            "thread_key": "main", "turn_id": "turn-question", "message": "help choose",
        },
    )
    assert first.status_code == 200
    assert first.json()["status"] == "waiting_input"
    store = AgentTurnStore(state)
    turn = store.get("turn-question")
    assert turn["state"] == "waiting_input"
    interaction = store.list_interactions(turn_id="turn-question", status="pending")[0]
    answered = client.post(
        f"/api/projects/default/webchat/interactions/{interaction['interaction_id']}/answer",
        headers={"x-zf-web-token": "test-token"},
        json={"revision": interaction["revision"], "response": {"answer": "small"}},
    )
    assert answered.status_code == 200
    assert answered.json()["resume"]["status"] == "accepted"
    _wait(lambda: store.get("turn-question")["state"] == "completed")
    attempts = store.attempts("turn-question")
    assert [item["attempt_no"] for item in attempts] == [1, 2]
    assert attempts[1]["resumed_from_attempt_id"] == attempts[0]["attempt_id"]


def test_codex_native_question_becomes_one_durable_pending_interaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    script = _fake_codex_native_question_script(tmp_path)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_WEBCHAT_RUNTIME", "durable")
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD", f"{sys.executable} {script}",
    )
    client = TestClient(create_app(state, project_root=tmp_path))

    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "codex-headless",
            "sync": True,
            "project_id": "default",
            "conversation_id": "conversation-native",
            "thread_key": "main",
            "turn_id": "turn-native-question",
            "message": "ask before choosing the route",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "waiting_input"
    store = AgentTurnStore(state)
    turn = store.get("turn-native-question")
    assert turn["state"] == "waiting_input"
    pending = store.list_interactions(
        turn_id="turn-native-question", status="pending",
    )
    assert len(pending) == 1
    assert pending[0]["request"]["questions"][0]["question"] == "Which route?"
    runtime = client.get(
        "/api/projects/default/webchat/runtime",
        params={"conversation_id": "conversation-native", "thread_id": "main"},
    )
    assert runtime.status_code == 200
    restored = runtime.json()["interactions"]
    assert [(item["interaction_id"], item["revision"]) for item in restored] == [
        (pending[0]["interaction_id"], pending[0]["revision"]),
    ]


def test_rollout_switch_uses_legacy_execution_without_mutating_durable_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    script = tmp_path / "fake_legacy_provider.py"
    script.write_text(
        "\n".join([
            "import json, sys",
            "sys.stdin.readline()",
            "print(json.dumps({'type':'system','session_id':'legacy-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'legacy-session','result':'legacy done'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_WEBCHAT_RUNTIME", "legacy")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless", "sync": True,
            "project_id": "default", "conversation_id": "conversation-a",
            "thread_key": "main", "turn_id": "legacy-turn", "message": "legacy",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert AgentTurnStore(state).list_turns(project_id="default") == []


def test_startup_recovery_creates_one_new_fenced_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    marker = tmp_path / "provider-calls.txt"
    script = tmp_path / "fake_recovery_provider.py"
    script.write_text(
        "\n".join([
            "import json, pathlib, sys",
            "sys.stdin.readline()",
            f"path = pathlib.Path({str(marker)!r})",
            "path.write_text(path.read_text() + 'call\\n' if path.exists() else 'call\\n')",
            "print(json.dumps({'type':'system','session_id':'recovered-session'}), flush=True)",
            "print(json.dumps({'type':'result','session_id':'recovered-session','result':'recovered'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_KANBAN_WEBCHAT_RUNTIME", "durable")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    store = AgentTurnStore(state)
    turn = store.submit(
        project_id="default", conversation_id="conversation-a", thread_id="main",
        backend="claude-headless", permission_profile="read_only",
        idempotency_key="recover-turn", turn_id="recover-turn", input_message_id="input-1",
        request={
            "message": "recover this turn",
            "payload": {
                "project_id": "default", "conversation_id": "conversation-a",
                "thread_key": "main", "backend": "claude-headless",
                "permission_profile": "read_only", "message": "recover this turn",
            },
        },
    )[0]
    old_claim = store.claim(turn["turn_id"], owner="dead-web", lease_seconds=120)
    with connect(state) as conn:
        conn.execute(
            "UPDATE webchat_attempt SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE attempt_id = ?",
            (old_claim.attempt_id,),
        )
    app = create_app(state, project_root=tmp_path)
    assert app.state.webchat_recovery_scheduled == 1
    _wait(lambda: store.get("recover-turn")["state"] == "completed")
    attempts = store.attempts("recover-turn")
    assert [item["attempt_no"] for item in attempts] == [1, 2]
    assert attempts[1]["resumed_from_attempt_id"] == old_claim.attempt_id
    with pytest.raises(Exception, match="stale"):
        store.settle(old_claim, state="completed")
    second = create_app(state, project_root=tmp_path)
    assert second.state.webchat_recovery_scheduled == 0
    assert marker.read_text(encoding="utf-8").splitlines() == ["call"]


def test_invalid_combined_plan_and_action_is_preserved_but_not_actionable(tmp_path: Path) -> None:
    state = _state(tmp_path)
    run = prepare_durable_webchat_run(
        state_dir=state, project_root=tmp_path,
        payload={"project_id": "default", "conversation_id": "c", "thread_key": "main"},
        message="design it", backend="codex-headless", permission_profile="read_only",
        turn_id="turn-invalid", user_message_id="msg-invalid",
    )
    settlement = settle_durable_reply(
        state_dir=state, run=run, reply="I need input and approval.",
        provider_session_id="session-invalid", ok=True,
        plan_request={"question": "Which scope?"},
        action_proposal={"action": "create-task", "intent": {}},
    )
    assert settlement["state"] == "failed"
    assert settlement["errors"] == ["plan_request and action_proposal are mutually exclusive"]
    assert AgentTurnStore(state).list_interactions(turn_id=run.turn_id) == []
    artifact = settlement["invalid_artifact"]
    body = AgentArtifactStore(state).read(
        artifact["version_id"], principal="kanban-agent",
    )[1].decode("utf-8")
    assert "mutually exclusive" in body
    messages = AgentConversationStore(state).list_messages(
        project_id="default", conversation_id="c", thread_id="main",
    )["items"]
    assert [item["role"] for item in messages] == ["user", "assistant"]


def test_native_and_legacy_question_dedupe_and_large_tool_output_uses_artifact(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    run = prepare_durable_webchat_run(
        state_dir=state, project_root=tmp_path,
        payload={"project_id": "default", "conversation_id": "c", "thread_key": "main"},
        message="inspect it", backend="codex-headless", permission_profile="read_only",
        turn_id="turn-native-dedupe", user_message_id="msg-native-dedupe",
    )
    recorder = DurablePartRecorder(state_dir=state, run=run)
    native = {
        "schema_version": "webchat-native-question.v1",
        "origin": "codex_request_user_input",
        "request_id": "question-1",
        "header": "Scope",
        "id": "scope",
        "question": "Choose scope",
        "options": [{"id": "small", "label": "Small"}, {"id": "large", "label": "Large"}],
    }
    recorder(HeadlessMessage(
        type="question", content="Choose scope",
        raw={"native_interaction": native},
    ))
    recorder(HeadlessMessage(type="tool_result", content="x" * 20_000))
    settlement = settle_durable_reply(
        state_dir=state, run=run, reply="Waiting for scope.",
        provider_session_id="native-session", ok=True,
        plan_request=native,
    )

    assert settlement["state"] == "waiting_input"
    interactions = AgentTurnStore(state).list_interactions(turn_id=run.turn_id)
    assert len(interactions) == 1
    parts = AgentTurnStore(state).replay_parts(run.turn_id, limit=20)["items"]
    tool = next(item for item in parts if item["kind"] == "tool_result")
    assert tool["content_ref"].startswith("artifact://")
    artifact_id = tool["refs"]["artifact_version_id"]
    artifact, body = AgentArtifactStore(state).read(
        artifact_id, principal="kanban-agent",
    )
    assert artifact["size_bytes"] == 20_000
    assert body == b"x" * 20_000


def test_native_and_legacy_question_conflict_fails_closed(tmp_path: Path) -> None:
    state = _state(tmp_path)
    run = prepare_durable_webchat_run(
        state_dir=state, project_root=tmp_path,
        payload={"project_id": "default", "conversation_id": "c", "thread_key": "main"},
        message="inspect it", backend="codex-headless", permission_profile="read_only",
        turn_id="turn-native-conflict", user_message_id="msg-native-conflict",
    )
    recorder = DurablePartRecorder(state_dir=state, run=run)
    recorder(HeadlessMessage(
        type="question", content="Choose scope",
        raw={"native_interaction": {
            "request_id": "question-1",
            "id": "scope",
            "question": "Choose scope",
            "options": [
                {"id": "small", "label": "Small"},
                {"id": "large", "label": "Large"},
            ],
        }},
    ))

    settlement = settle_durable_reply(
        state_dir=state, run=run, reply="Waiting for a decision.",
        provider_session_id="native-session", ok=True,
        plan_request={
            "id": "route",
            "question": "Choose route",
            "options": [
                {"id": "research", "label": "Research"},
                {"id": "build", "label": "Build"},
            ],
        },
    )

    assert settlement["state"] == "failed"
    assert settlement["errors"] == [
        "provider-native question and legacy plan_request conflict"
    ]
    assert len(AgentTurnStore(state).list_interactions(turn_id=run.turn_id)) == 1


def test_retry_reuses_immutable_input_without_duplicate_user_message(tmp_path: Path) -> None:
    state = _state(tmp_path)
    first = prepare_durable_webchat_run(
        state_dir=state, project_root=tmp_path,
        payload={"project_id": "default", "conversation_id": "c", "thread_key": "main"},
        message="original exact input", backend="codex-headless", permission_profile="read_only",
        turn_id="turn-first", user_message_id="msg-source",
    )
    first_settlement = settle_durable_reply(
        state_dir=state, run=first, reply="first result",
        provider_session_id="session-first", ok=True,
    )
    assistant_id = first_settlement["message"]["message_id"]
    retry = prepare_durable_webchat_run(
        state_dir=state, project_root=tmp_path,
        payload={
            "project_id": "default", "conversation_id": "c", "thread_key": "main",
            "retry_message_id": assistant_id,
        },
        message="original exact input", backend="codex-headless", permission_profile="read_only",
        turn_id="turn-retry", user_message_id="new-legacy-message",
    )
    assert retry.user_message_id == "msg-source"
    assert AgentTurnStore(state).get("turn-retry")["input_message_id"] == "msg-source"
    messages = AgentConversationStore(state).list_messages(
        project_id="default", conversation_id="c", thread_id="main",
    )["items"]
    assert [item["role"] for item in messages] == ["user", "assistant"]


def test_retry_from_fork_reuses_visible_parent_input_without_copying_prefix(tmp_path: Path) -> None:
    state = _state(tmp_path)
    first = prepare_durable_webchat_run(
        state_dir=state, project_root=tmp_path,
        payload={"project_id": "default", "conversation_id": "c", "thread_key": "main"},
        message="original input", backend="codex-headless", permission_profile="read_only",
        turn_id="turn-parent", user_message_id="msg-parent-input",
    )
    first_result = settle_durable_reply(
        state_dir=state, run=first, reply="parent result",
        provider_session_id="session-parent", ok=True,
    )
    assistant_id = first_result["message"]["message_id"]
    fork = AgentConversationStore(state).fork(
        assistant_id, title="Alternative", project_id="default",
    )
    retry = prepare_durable_webchat_run(
        state_dir=state, project_root=tmp_path,
        payload={
            "project_id": "default", "conversation_id": "c",
            "thread_key": fork["thread_id"], "retry_message_id": assistant_id,
        },
        message="original input", backend="codex-headless", permission_profile="read_only",
        turn_id="turn-fork-retry", user_message_id="must-not-be-used",
    )
    assert retry.user_message_id == "msg-parent-input"
    visible = AgentConversationStore(state).list_messages(
        project_id="default", conversation_id="c", thread_id=fork["thread_id"],
    )["items"]
    assert [item["message_id"] for item in visible] == ["msg-parent-input", assistant_id]


def test_interrupt_starts_one_new_fenced_attempt_on_same_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path)
    script = tmp_path / "fake_interrupt_provider.py"
    script.write_text(
        "\n".join([
            "import json, sys, time",
            "line = sys.stdin.readline()",
            "prompt = json.loads(line).get('prompt', line) if line.strip().startswith('{') else line",
            "print(json.dumps({'type':'system','session_id':'interrupt-session'}), flush=True)",
            "if 'operator steer' not in prompt:",
            "    time.sleep(20)",
            "print(json.dumps({'type':'result','session_id':'interrupt-session','result':'resumed'}), flush=True)",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    client = TestClient(create_app(state, project_root=tmp_path))
    started = client.post(
        "/api/actions/chat-orchestrator",
        headers={"x-zf-web-token": "test-token"},
        json={
            "backend": "claude-headless", "sync": False,
            "project_id": "default", "conversation_id": "conversation-a",
            "thread_key": "main", "turn_id": "turn-interrupt", "message": "slow work",
        },
    )
    assert started.status_code == 202
    store = AgentTurnStore(state)
    _wait(lambda: store.get("turn-interrupt")["state"] == "running")
    interrupted = client.post(
        "/api/projects/default/webchat/turns/turn-interrupt/control",
        headers={"x-zf-web-token": "test-token"},
        json={
            "operation": "interrupt", "message": "use the smaller implementation",
            "idempotency_key": "interrupt-once",
        },
    )
    assert interrupted.status_code == 200
    assert interrupted.json()["resume"]["status"] == "accepted"
    _wait(lambda: store.get("turn-interrupt")["state"] == "completed")
    attempts = store.attempts("turn-interrupt")
    assert [item["attempt_no"] for item in attempts] == [1, 2]
    assert attempts[0]["state"] == "abandoned"
    assert attempts[1]["resumed_from_attempt_id"] == attempts[0]["attempt_id"]
    duplicate = client.post(
        "/api/projects/default/webchat/turns/turn-interrupt/control",
        headers={"x-zf-web-token": "test-token"},
        json={
            "operation": "interrupt", "message": "use the smaller implementation",
            "idempotency_key": "interrupt-once",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert len(store.attempts("turn-interrupt")) == 2
