"""Unknown delivery stays fenced until an Owner abandons a stopped attempt."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tests.test_channel_synthesis_recovery import seed
from zf.core.state.locks import locked_path
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_remediation import channel_reply_remediation_candidates
from zf.runtime.channel_run_owner import active_reply_for_target, reply_generation_is_current
from zf.runtime.channel_synthesis_currentness import synthesis_request_currentness
from zf.runtime.control_actions import ControlledActionService


def world(tmp_path, *, terminal=True):
    state, writer, emit = seed(tmp_path, failure_status="delivery_unknown")
    run = project_channel(state, "ch-recovery")["reply_requests"][0]
    if terminal:
        emit("agent.session.run.failed", run_id=run["run_id"], request_id="reply-old",
             run_generation=3, status="provider_timeout", reason="total deadline")
    service = ControlledActionService(state, writer, project_root=tmp_path,
                                     actor="web", source="web", surface="web")
    return state, writer, emit, service, run


def recover(emit, service, **payload):
    requested = emit("runtime.action.requested", action="channel-synthesis-recover")
    return service.execute(action="channel-synthesis-recover",
                           requested_action="channel.synthesis.recover", requested=requested,
                           payload={"channel_id": "ch-recovery", "thread_id": "main",
                                    "synthesis_request_id": "synth-old", **payload})


def test_recovery_preserves_unknown_evidence_and_only_releases_manual_retry(tmp_path, monkeypatch):
    state, writer, emit, service, run = world(tmp_path)
    dispatched = []
    monkeypatch.setattr("zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background",
                        lambda **kw: dispatched.append(kw))
    before = project_channel(state, "ch-recovery")
    assert before["synthesis_requests"][-1]["recovery_required"]
    result = recover(emit, service)
    assert result["ok"] and result["status"] == "recovered"
    after = project_channel(state, "ch-recovery")
    reply = after["reply_requests"][0]
    assert reply["failure_status"] == "delivery_unknown"
    assert reply["terminal_disposition"] == "abandoned"
    assert not active_reply_for_target(after, "synth", statuses=())
    assert after["synthesis_requests"][-1]["status"] == "cancelled"
    assert not after["synthesis_requests"][-1]["recovery_required"]
    assert after["messages"] == before["messages"]
    assert after["syntheses"] == before["syntheses"]
    assert after["discussions"] == before["discussions"]
    assert not dispatched
    assert not channel_reply_remediation_candidates(writer.event_log.read_all())
    events = writer.event_log.read_all()
    receipt = next(e for e in events if e.type == "channel.synthesis.recovered")
    binding = receipt.payload["replies"][0]
    assert binding["request_id"] == "reply-old"
    assert binding["run_id"] == run["run_id"] and binding["run_generation"] == 3
    assert binding["terminal_event_id"] and binding["failure_event_id"]
    assert not reply_generation_is_current(events, channel_id="ch-recovery", request=run)
    assert synthesis_request_currentness(events, channel_id="ch-recovery", thread_id="main",
                                        request_id="synth-old", session=after["discussions"]["main"])
    assert recover(emit, service)["status"] == "already_recovered"
    assert len([e for e in writer.event_log.read_all() if e.type == "channel.synthesis.recovered"]) == 1


@pytest.mark.parametrize("change", ["missing_terminal", "old_generation", "wrong_run", "wrong_channel", "running"])
def test_recovery_requires_matching_terminal_evidence(tmp_path, change):
    state, writer, emit, service, run = world(tmp_path, terminal=False)
    if change != "missing_terminal":
        emit("agent.session.run.started" if change == "running" else "agent.session.run.failed",
             run_id="wrong" if change == "wrong_run" else run["run_id"], request_id="reply-old",
             run_generation=2 if change == "old_generation" else 3,
             channel_id="other" if change == "wrong_channel" else "ch-recovery")
    result = recover(emit, service)
    assert not result["ok"] and result["_status_code"] == 409
    assert active_reply_for_target(project_channel(state, "ch-recovery"), "synth", statuses=())
    assert not any(e.type == "channel.synthesis.recovered" for e in writer.event_log.read_all())


@pytest.mark.parametrize("payload,actor", [({"thread_id": "other"}, "web"), ({"synthesis_request_id": "wrong"}, "web"), ({}, "agent")])
def test_recovery_checks_scope_and_owner(tmp_path, payload, actor):
    state, writer, emit, service, run = world(tmp_path)
    service.actor = actor
    result = recover(emit, service, **payload)
    assert not result["ok"]
    assert not any(e.type == "channel.synthesis.recovered" for e in writer.event_log.read_all())


def test_active_dispatch_claim_blocks_recovery_even_with_failed_projection(tmp_path):
    from zf.runtime.channel_dispatch_claim import channel_reply_dispatch_claim_path
    state, writer, emit, service, run = world(tmp_path)
    held, release = Event(), Event()
    def hold():
        with locked_path(channel_reply_dispatch_claim_path(state, "ch-recovery", "reply-old")):
            held.set()
            assert release.wait(10)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(hold)
        assert held.wait(3)
        try:
            result = recover(emit, service)
            assert not result["ok"] and result["status"] == "provider_still_running"
        finally:
            release.set()
        future.result()
    assert recover(emit, service)["ok"]


def test_abandoned_attempt_cannot_be_resurrected_by_late_failure_or_redispatch(tmp_path):
    state, writer, emit, service, run = world(tmp_path)
    assert recover(emit, service)["ok"]
    emit("channel.agent.reply.failed", request_id="reply-old", run_generation=3,
         failure_status="delivery_unknown", reason="late error")
    emit("channel.agent.reply.requested", request_id="reply-old", run_generation=4)
    reply = project_channel(state, "ch-recovery")["reply_requests"][0]
    assert reply["terminal_disposition"] == "abandoned" and reply["run_generation"] == 3
    assert not channel_reply_remediation_candidates(writer.event_log.read_all())
    assert not reply_generation_is_current(writer.event_log.read_all(), channel_id="ch-recovery",
                                           request={**run, "run_generation": 4})


def test_existing_publication_cannot_be_abandoned(tmp_path):
    state, writer, emit, service, run = world(tmp_path)
    emit("channel.synthesis.proposed", request_id="synth-old", artifact_ref="existing.json",
         artifact_digest="existing", prd_revision=1)
    assert not recover(emit, service)["ok"]
    assert not any(e.type == "channel.synthesis.recovered" for e in writer.event_log.read_all())


def test_concurrent_recovery_records_one_receipt(tmp_path):
    state, writer, emit, service, run = world(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recover(emit, service), range(2)))
    assert sorted(r["status"] for r in results) == ["already_recovered", "recovered"]
    assert len([e for e in writer.event_log.read_all() if e.type == "channel.synthesis.recovered"]) == 1


def test_new_start_after_terminal_is_not_treated_as_stopped(tmp_path):
    state, writer, emit, service, run = world(tmp_path)
    emit("agent.session.run.started", run_id=run["run_id"], request_id="reply-old", run_generation=3)
    assert not recover(emit, service)["ok"]


def test_new_synthesis_after_recovery_preserves_original_materials(tmp_path, monkeypatch):
    from tests.test_channel_libre import world as libre_world, post, execute
    from zf.runtime.channel_prd_snapshot import snapshot_channel
    state, writer = libre_world.__wrapped__(tmp_path)
    service = ControlledActionService(state, writer, project_root=tmp_path, actor="web", source="web", surface="web")
    monkeypatch.setattr("zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background", lambda **kw: None)
    post((state, writer), text="原始构想")
    original = execute(service, writer, "channel-synthesis-request", {"channel_id": "ch-free", "additional_material": "原始补充"})
    cid, rid = "ch-free", original["request_id"]
    channel = project_channel(state, cid)
    reply = next(r for r in channel["reply_requests"]
                 if any(m["message_id"] == r["message_id"] and (m.get("refs") or {}).get("synthesis_request_id") == rid
                        for m in channel["messages"]))
    writer.emit("channel.agent.reply.failed", actor="test", payload={**reply, "channel_id": cid,
                "failure_status": "delivery_unknown", "retryable": False})
    writer.emit("agent.session.run.failed", actor="test", payload={**reply, "channel_id": cid})
    result = execute(service, writer, "channel-synthesis-recover", {"channel_id": cid, "synthesis_request_id": rid})
    assert result["ok"]
    post((state, writer), mid="later", text="之后的内容")
    retry = execute(service, writer, "channel-synthesis-request", {"channel_id": cid, "retry_of_request_id": rid})
    assert retry["ok"] and retry["request_id"] != rid
    frozen = snapshot_channel(state, project_channel(state, cid), retry["request_id"])
    assert frozen["prd_additional_material"] == "原始补充"
    assert not any(m.get("text") == "之后的内容" for m in frozen["messages"])


def test_registered_http_recovery_requires_token_and_does_not_generate(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from zf.core.task.store import TaskStore
    from zf.web.server import create_app
    state, writer, emit, service, run = world(tmp_path)
    TaskStore(state / "kanban.json")._save_raw([])
    emit("channel.discussion.mode.set", mode="libre_discuss", default_responder_id="synth")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "recovery-test-token")
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-registry"))
    client = TestClient(create_app(state, project_root=tmp_path))
    project = client.get("/api/workspace/projects").json()["active_project_id"]
    path = f"/api/projects/{project}/actions/channel.synthesis.recover"
    conversation = f"/api/projects/{project}/channels/ch-recovery/conversation"
    assert client.get(conversation).json()["synthesis_requests"][-1]["recovery_required"] is True
    payload = {"project_id": project, "actor": "web", "payload": {
        "channel_id": "ch-recovery", "thread_id": "main", "synthesis_request_id": "synth-old"}}
    assert client.post(path, json=payload).status_code == 403
    response = client.post(path, json=payload,
                           headers={"X-ZF-Web-Token": "recovery-test-token"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "recovered"
    assert client.get(conversation).json()["synthesis_requests"][-1]["recovery_required"] is False
    assert len(project_channel(state, "ch-recovery")["reply_requests"]) == 1
    assert not any(e.type.startswith("workflow.") or e.type == "task.created"
                   for e in writer.event_log.read_all())
