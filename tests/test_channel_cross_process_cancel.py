"""Web and Harness registries are separate; the ledger carries stop intent."""
import multiprocessing
import sys
import time

from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.channel_projection import project_channel
from zf.web.server import create_app


def run_provider(state, root, script, result):
    from zf.runtime.channel_adapter import _run_headless_reply
    from zf.web.headless_agent import ClaudeHeadlessBackend
    writer = EventWriter(EventLog(state / "events.jsonl"))
    channel = project_channel(state, "ch-stop")
    turn = _run_headless_reply(state_dir=state, project_root=root, writer=writer, channel=channel,
        member=channel["members"][0], message=channel["messages"][0], request=channel["reply_requests"][0],
        request_id="reply-stop", started_event_id="started", actor="runtime", source="test",
        backend="claude-headless", backends={"claude-headless": ClaudeHeadlessBackend(command=f"{sys.executable} {script}")})
    result.put((turn.ok, turn.status, turn.reply))


def test_web_stop_reaches_harness_owned_subprocess_and_fences_late_output(tmp_path, monkeypatch):
    state = tmp_path / "runtime"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    TaskStore(state / "kanban.json")._save_raw([])
    def emit(kind, **p):
        return writer.emit(kind, actor="web", payload={"channel_id": "ch-stop", "thread_id": "main", **p})
    emit("channel.created", owner_actor_ref="web")
    emit("channel.member.invited", member_id="arch", member_type="provider_agent", backend="claude", permission_profile="read_only")
    emit("channel.message.posted", message_id="user", role="user", text="Read the requirement")
    emit("channel.agent.reply.requested", request_id="reply-stop", message_id="user", target_member_id="arch", run_generation=1)
    script = tmp_path / "provider.py"
    script.write_text("import json,sys,time\nsys.stdin.readline()\nprint(json.dumps({'type':'system','session_id':'session'}),flush=True)\nprint(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'working'}]}}),flush=True)\ntime.sleep(10)\nprint(json.dumps({'type':'result','result':'too late'}),flush=True)\n")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "cross-process-stop")
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    ctx = multiprocessing.get_context("spawn")
    result = ctx.Queue()
    owner = ctx.Process(target=run_provider, args=(state, tmp_path, script, result))
    owner.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            events = writer.event_log.read_all()
            if any(e.type == "agent.session.part.delta" for e in events):
                break
            time.sleep(0.03)
        public = next(e.payload for e in events if e.type == "agent.session.run.started")
        with TestClient(create_app(state, project_root=tmp_path)) as client:
            project = client.get("/api/workspace/projects").json()["active_project_id"]
            stale = client.post(f"/api/projects/{project}/actions/agent-session-cancel",
                headers={"X-ZF-Web-Token": "cross-process-stop"}, json={"project_id": project, "actor": "web", "payload": {
                    "run_id": public["run_id"], "thread_id": public["thread_id"], "channel_id": "ch-stop",
                    "conversation_id": "ch-stop", "run_generation": 2}})
            assert stale.status_code == 409
            assert not any(e.type == "agent.session.run.cancelled" for e in writer.event_log.read_all())
            response = client.post(f"/api/projects/{project}/actions/agent-session-cancel",
                headers={"X-ZF-Web-Token": "cross-process-stop"}, json={"project_id": project, "actor": "web", "payload": {
                    "run_id": public["run_id"], "thread_id": public["thread_id"], "channel_id": "ch-stop",
                    "conversation_id": "ch-stop", "run_generation": 1}})
            assert response.status_code == 200, response.text
            assert not response.json()["process_found"]  # Registry is in the other process.
        owner.join(8)
        assert not owner.is_alive()
        ok, status, reply = result.get(timeout=1)
        assert not ok and status == "cancelled" and "too late" not in reply
        assert project_channel(state, "ch-stop")["provider_runs"][0]["status"] == "cancelled"
    finally:
        if owner.is_alive():
            owner.terminate()
            owner.join(2)


def test_web_cancel_fences_only_its_current_generation_before_late_publication():
    from zf.core.events import ZfEvent
    from zf.runtime.channel_run_owner import provider_run_fields_for_request, reply_generation_is_current
    request = {"request_id": "reply", "target_member_id": "arch", "thread_id": "main", "run_generation": 2}
    fields = provider_run_fields_for_request("ch", request)
    events = [ZfEvent(type="channel.agent.reply.requested", actor="runtime", payload={"channel_id": "ch", **request})]
    events.append(ZfEvent(type="agent.session.run.cancelled", actor="web", payload={
        "channel_id": "ch", "source": "web", "run_id": fields["run_id"], "run_generation": 1}))
    assert reply_generation_is_current(events, channel_id="ch", request=request)
    events.append(ZfEvent(type="agent.session.run.cancelled", actor="web", payload={
        "channel_id": "ch", "source": "web", "run_id": fields["run_id"], "run_generation": 2}))
    assert not reply_generation_is_current(events, channel_id="ch", request=request)
