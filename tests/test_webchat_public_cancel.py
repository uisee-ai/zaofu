"""The public Kanban run id cancels and fences its actual provider attempt."""

import sys
import time

from fastapi.testclient import TestClient

from zf.core.events import EventLog
from zf.web.agent_turn_store import AgentTurnStore
from zf.web.server import create_app


def test_public_cancel_interrupts_durable_attempt_and_survives_restart(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    script = tmp_path / "provider.py"
    script.write_text(
        "import json,sys,time\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type':'system','session_id':'cancel-public'}),flush=True)\n"
        "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'working'}]}}),flush=True)\n"
        "time.sleep(30)\n"
        "print(json.dumps({'type':'result','result':'late answer'}),flush=True)\n"
    )
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", f"{sys.executable} {script}")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-public-cancel")
    headers = {"x-zf-web-token": "test-public-cancel"}
    with TestClient(create_app(state, project_root=tmp_path)) as client:
        started = client.post("/api/actions/chat-orchestrator", headers=headers, json={
            "backend": "claude-headless", "sync": False, "project_id": "default",
            "conversation_id": "kanban:default", "thread_key": "main",
            "turn_id": "turn-public-cancel", "message": "Explain the current project without changing anything.",
        })
        assert started.status_code == 202, started.text
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            events = EventLog(state / "events.jsonl").read_all()
            if any(e.type == "agent.session.part.delta" for e in events):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("provider did not stream")
        public = next(e.payload for e in events if e.type == "agent.session.run.started")
        turns = AgentTurnStore(state)
        assert turns.get(public["run_id"])["current_attempt_id"] != public["run_id"]
        wrong = client.post("/api/actions/agent-session-cancel", headers=headers, json={
            "run_id": public["run_id"], "thread_id": "another-thread",
        })
        assert wrong.status_code == 409
        assert turns.get(public["run_id"])["state"] == "running"
        response = client.post("/api/actions/agent-session-cancel", headers=headers, json={
            "run_id": public["run_id"], "thread_id": public["thread_id"],
            "conversation_id": public["conversation_id"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["process_terminated"], response.json()
        assert turns.get(public["run_id"])["state"] == "cancelled"
    with TestClient(create_app(state, project_root=tmp_path)):
        assert AgentTurnStore(state).get(public["run_id"])["state"] == "cancelled"
        assert len(AgentTurnStore(state).attempts(public["run_id"])) == 1
        events = EventLog(state / "events.jsonl").read_all()
        assert not any(e.type == "task.created" for e in events)
        assert not any(e.type == "agent.session.run.completed" for e in events)
