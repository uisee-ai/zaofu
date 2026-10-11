"""Plan answers keep origin identity while binding the new operator input."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter
from zf.web.headless_agent import HeadlessTurnResult
from zf.web.server import create_app


def test_owner_plan_answer_survives_restart_and_binds_current_sources(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    EventWriter(EventLog(state / "events.jsonl")).emit("loop.started", actor="test")
    captured = []

    def run_turn(**kwargs):
        sources = kwargs["context"]["requirement_source_messages"]
        captured.append(sources)
        request = {
            "header": "Scope", "subject_type": "clarification", "question": "Choose scope",
            "originating_message_event_ids": [row["event_id"] for row in sources],
            "options": [{"id": "small", "label": "Small (Recommended)"},
                        {"id": "large", "label": "Large"}],
        }
        return HeadlessTurnResult(
            ok=True, status="completed", backend="codex-headless", thread_id="main",
            provider_session_id="test-session", reply=json.dumps({"plan_request": request}),
            messages=[], usage={},
        )

    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    monkeypatch.setattr("zf.web.observability.create_observed_headless_agent",
                        lambda **kwargs: SimpleNamespace(run_turn=run_turn))
    headers = {"x-zf-web-token": "test-token"}
    scope = {"backend": "codex-headless", "sync": True, "project_id": "default",
             "conversation_id": "kanban:default", "thread_key": "main"}
    first = TestClient(create_app(state, project_root=tmp_path)).post(
        "/api/actions/chat-orchestrator", headers=headers,
        json={**scope, "message": "Plan the new editor."},
    )
    assert first.status_code == 200, first.text
    initial = first.json()["reply"]["plan_request"]
    assert initial["valid"], initial["validation_error"]
    # Recreate the Web entrypoint before consuming the ledger-bound answer.
    reloaded = TestClient(create_app(state, project_root=tmp_path))
    answered = reloaded.post("/api/actions/chat-orchestrator", headers=headers, json={
        **scope, "plan_response": {
            "request_event_id": initial["request_event_id"], "request_id": initial["request_id"],
            "revision": initial["revision"], "question_id": initial["question_id"],
            "option_id": "small",
        },
    })
    assert answered.status_code == 200, answered.text
    current = answered.json()["reply"]["plan_request"]
    assert len(captured[-1]) == 2
    assert current["valid"], current["validation_error"]
    assert current["originating_message_event_id"] == initial["originating_message_event_id"]
    assert current["originating_message_event_ids"] == [row["event_id"] for row in captured[-1]]
    events = EventLog(state / "events.jsonl").read_all()
    assert not any(e.type in {"task.created", "run.started"} for e in events)
