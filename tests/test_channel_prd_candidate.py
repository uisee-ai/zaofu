"""Rejected candidates remain readable without becoming formal PRD artifacts."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_libre import libre_conversation_metadata
from zf.runtime.channel_projection import project_channel
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.web.channel_prd_candidate import build_channel_prd_candidate_router


@pytest.mark.parametrize("structured", [False, True])
def test_candidate_is_bound_read_only_and_integrity_checked(tmp_path, structured):
    state = tmp_path / "runtime"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch", "name": "Candidate", "discussion_mode": "libre_discuss"})
    raw = json.dumps({"channel_synthesis": {"prd_markdown": "# 待修订候选稿\n仍缺验收证据"}}, ensure_ascii=False) if structured else "# 待修订候选稿\n仍缺验收证据"
    ref = write_sidecar_text(state, Path("channels/ch/candidate.txt"), raw, kind="channel_synthesis_invalid_reply",
                             schema_version="channel.synthesis.invalid-reply.v1", created_by="synth",
                             source_event_id="reply", access_scope={"channel_id": "ch", "thread_id": "main"}, required=True)
    writer.emit("channel.synthesis.blocked", actor="synth", payload={"channel_id": "ch", "thread_id": "main",
                "request_id": "generation", "invalid_reply_ref": ref, "reason": "missing evidence"})
    before = (state / "events.jsonl").read_bytes()
    app = FastAPI()
    app.include_router(build_channel_prd_candidate_router(resolve_ctx=lambda _: SimpleNamespace(state_dir=state)))
    client = TestClient(app)
    path = "/api/projects/default/channels/ch/prd-candidate"
    response = client.get(path, params={"request_id": "generation"})
    assert response.status_code == 200
    assert response.json()["status"] == "unvalidated_candidate"
    assert response.json()["markdown"].startswith("# 待修订候选稿")
    for params in ({"request_id": "other"}, {"request_id": "generation", "thread_id": "other"}):
        assert client.get(path, params=params).status_code == 404
    assert (state / "events.jsonl").read_bytes() == before
    channel = project_channel(state, "ch")
    assert libre_conversation_metadata(channel, set())["synthesis_requests"][0]["candidate_available"]
    assert not channel.get("syntheses")
    (state / "channels/ch/candidate.txt").write_text("tampered")
    assert client.get(path, params={"request_id": "generation"}).status_code == 409
