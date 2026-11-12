"""The controlled request, real adapter and original publication gate close together."""
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from tests.test_channel_libre import world, post, execute
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_provider_prompt import channel_context_pack_by_id
from zf.runtime.channel_reply_prompt import fake_channel_reply_text
from zf.runtime.channel_synthesis_parts import PARTS
from zf.runtime.control_actions import ControlledActionService
from zf.web.headless_agent import HeadlessTurnResult
from zf.web.channel_prd_candidate import build_channel_prd_candidate_router
from zf.runtime.channel_libre import libre_conversation_metadata
from zf.runtime.channel_conversation_projection import project_channel_conversation


class PortionBackend:
    backend_id = "codex-headless"

    def __init__(self, state, *, fail="", wrong_attestation=False):
        self.state, self.fail, self.wrong_attestation = state, fail, wrong_attestation
        self.calls = []

    def available(self):
        return True

    def run_turn(self, **kwargs):
        name = re.search(r"ONLY part '([^']+)'", kwargs["prompt"]).group(1)
        self.calls.append(kwargs)
        kwargs["on_session_id"]("isolated-native-session")
        if name == self.fail:
            return HeadlessTurnResult(ok=False, status="failed", backend=self.backend_id,
                thread_id="main", provider_session_id="isolated-native-session", reply="", messages=[],
                usage={}, error="Provider disconnected before completion", telemetry={"input_delivery": "unknown"})
        channel = project_channel(self.state, "ch-free")
        request = channel["reply_requests"][-1]
        pack = channel_context_pack_by_id(channel, request["context_pack_id"], state_dir=self.state)
        message = next(m for m in channel["messages"] if m["message_id"] == request["message_id"])
        body = json.loads(fake_channel_reply_text({"member_id": "synth"}, message).split("<!-- zf:channel-contract -->\n")[1])["channel_synthesis"]
        spec = next(p for p in PARTS if p.name == name)
        fields = {key: val for key, val in body.items() if key in spec.fields}
        for keys, value in ((spec.arrays, []), (spec.objects, {}), (spec.strings, "fixture")):
            for key in keys:
                fields.setdefault(key, value)
        if name == "finalize":
            fields["next_round"] = {"action": "finalize"}
            for key in ("consumed_message_digests", "consumed_contribution_refs", "consumed_contribution_digests"):
                fields.pop(key, None)
            fields["source_attestation"] = {"context_pack_id": pack["context_pack_id"],
                "context_pack_sha256": "wrong" if self.wrong_attestation else pack["context_pack_sha256"],
                "all_required_sources_read": True}
        reply = "已完成当前部分。\n<!-- zf:channel-contract -->\n" + json.dumps({
            "channel_synthesis_part": {"part": name, "fields": fields}})
        return HeadlessTurnResult(ok=True, status="completed", backend=self.backend_id,
            thread_id="main", provider_session_id="isolated-native-session", reply=reply, messages=[], usage={})


def prepare(world, monkeypatch):
    state, writer = world
    monkeypatch.setattr("zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background", lambda **kw: None)
    writer.emit("channel.member.invited", actor="web", payload={"channel_id": "ch-free", "member_id": "synth",
        "channel_role": "synthesizer", "member_type": "provider_agent", "backend": "codex", "provider": "codex",
        "permission_profile": "read_only", "permissions": ["read", "message", "summarize"]})
    post(world, mentions=["pm"])
    service = ControlledActionService(state, writer, project_root=state.parent, actor="web", source="web", surface="web")
    return state, writer, service


def dispatch(state, writer, backend):
    channel = project_channel(state, "ch-free")
    return dispatch_reply_request(state_dir=state, writer=writer, channel_id="ch-free",
        request_id=channel["reply_requests"][-1]["request_id"], actor="test", source="test",
        project_root=state.parent, headless_backends={"codex-headless": backend})


@pytest.mark.parametrize("bad_attestation", [False, True])
def test_actual_adapter_parts_still_pass_through_original_publication_gates(world, monkeypatch, bad_attestation):
    state, writer, service = prepare(world, monkeypatch)
    result = execute(service, writer, "channel-synthesis-request", {"channel_id": "ch-free"})
    assert result["ok"], result
    backend = PortionBackend(state, wrong_attestation=bad_attestation)
    dispatch(state, writer, backend)
    assert len(backend.calls) == 5
    assert len({call["execution_deadline"] for call in backend.calls}) == 1
    events = writer.event_log.read_all()
    proposed = [e for e in events if e.type == "channel.synthesis.proposed"]
    assert len(proposed) == (0 if bad_attestation else 1), [(e.type, e.payload.get("reason")) for e in events[-10:]]
    assert len([e for e in events if e.type == "agent.session.part.delta" and
        (e.payload.get("refs", {}).get("raw", {}).get("activity", {}).get("checkpoint"))]) == 5
    projected = project_channel_conversation(state, "ch-free")
    trigger = next(m for m in projected["messages"] if (m.get("refs") or {}).get("synthesis_request_id"))
    assert trigger["refs"]["display_origin"] == "user_prd_request"
    assert "请按本次选定" in trigger["refs"]["display_text"]
    phases = [p["refs"]["raw"]["activity"] for r in projected["provider_runs"] for p in r["parts"]
              if (p.get("refs", {}).get("raw", {}).get("activity"))]
    assert len(phases) == 5 and phases[-1]["completed"] == 5
    assert all("checkpoint" not in p and "input_fingerprint" not in p for p in phases)
    assert not any(e.type in {"channel.consensus.reached", "task.created", "workflow.started"} for e in events)


@pytest.mark.parametrize("legacy", [False, True])
def test_actual_controlled_retry_restores_only_saved_parts(world, monkeypatch, legacy):
    state, writer, service = prepare(world, monkeypatch)
    first = execute(service, writer, "channel-synthesis-request", {"channel_id": "ch-free"})
    assert first["ok"], first
    backend = PortionBackend(state, fail="requirements")
    with monkeypatch.context() as first_turn:
        if legacy:
            from zf.runtime.channel_synthesis_checkpoints import binding_for
            first_turn.setattr("zf.runtime.channel_checkpointed_synthesis.binding_for", lambda *a: {
                key: value for key, value in binding_for(*a).items() if key != "method_digest"})
        dispatch(state, writer, backend)
    # Reading a saved portion is not a publication or a new generation.
    channel = project_channel(state, "ch-free")
    assert libre_conversation_metadata(channel, set())["synthesis_requests"][-1]["checkpoint_draft_available"]
    app = FastAPI()
    app.include_router(build_channel_prd_candidate_router(resolve_ctx=lambda _: SimpleNamespace(state_dir=state)))
    before = writer.event_log.path.read_bytes()
    with TestClient(app) as client:
        draft = client.get("/api/projects/default/channels/ch-free/prd-candidate", params={"request_id": first["request_id"]})
        assert draft.status_code == 200, draft.text
        assert draft.json()["status"] == "checkpoint_draft"
        assert "Deterministic test PRD" in draft.json()["markdown"]
        assert client.get("/api/projects/default/channels/ch-free/prd-candidate", params={"request_id": first["request_id"], "thread_id": "other"}).status_code == 404
    assert writer.event_log.path.read_bytes() == before
    ended = execute(service, writer, "channel-synthesis-recover", {"channel_id": "ch-free", "synthesis_request_id": first["request_id"]})
    assert ended["ok"], ended
    retry = execute(service, writer, "channel-synthesis-request", {"channel_id": "ch-free", "retry_of_request_id": first["request_id"]})
    assert retry["ok"], retry
    backend = PortionBackend(state)
    dispatch(state, writer, backend)
    names = [re.search(r"ONLY part '([^']+)'", c["prompt"]).group(1) for c in backend.calls]
    assert names == ["requirements", "decisions", "implementation", "finalize"]
    assert len([e for e in writer.event_log.read_all() if e.type == "channel.synthesis.proposed"]) == 1


@pytest.mark.parametrize("action", ["channel.synthesis.cancel", "channel-synthesis-cancel"])
def test_actual_web_cancel_alias_is_token_gated_and_fences_request(world, monkeypatch, action):
    from zf.core.task.store import TaskStore
    from zf.web.server import create_app
    state, writer, service = prepare(world, monkeypatch)
    first = execute(service, writer, "channel-synthesis-request", {"channel_id": "ch-free"})
    TaskStore(state / "kanban.json")._save_raw([])
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "cancel-test")
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(state.parent / "workspace"))
    with TestClient(create_app(state, project_root=state.parent)) as client:
        project = client.get("/api/workspace/projects").json()["active_project_id"]
        path = f"/api/projects/{project}/actions/{action}"
        payload = {"project_id": project, "actor": "web", "payload": {"channel_id": "ch-free", "request_id": first["request_id"]}}
        assert client.post(path, json=payload).status_code == 403
        response = client.post(path, json=payload, headers={"X-ZF-Web-Token": "cancel-test"})
        assert response.status_code == 202, response.text
    assert project_channel(state, "ch-free")["synthesis_requests"][-1]["status"] == "cancelled"
    assert not any(e.type in {"channel.synthesis.proposed", "task.created", "workflow.started"} for e in writer.event_log.read_all())
