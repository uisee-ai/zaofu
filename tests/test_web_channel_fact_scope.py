"""Owner scope exclusions preserve unresolved facts through the public action."""
import pytest
from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_projection import project_channel
from zf.web.server import create_app


@pytest.fixture
def scope_case(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-scope", "name": "Scope"})
    writer.emit("channel.member.invited", actor="web", payload={
        "channel_id": "ch-scope", "member_id": "arch", "member_type": "agent",
    })
    writer.emit("channel.question.opened", actor="arch", payload={
        "channel_id": "ch-scope", "thread_id": "main", "question_id": "q-fact",
        "question": "Has the future cancellation test passed?", "kind": "fact",
        "asked_by": "arch", "target_member_id": "arch",
    })
    writer.emit("channel.cross_review.requested", actor="arch", payload={
        "channel_id": "ch-scope", "thread_id": "main", "request_id": "review-fact",
        "question_id": "q-fact", "target_member_id": "arch",
    })
    writer.emit("channel.cross_review.completed", actor="arch", payload={
        "channel_id": "ch-scope", "thread_id": "main", "request_id": "review-fact",
        "outcome": "unresolved", "summary": "No run evidence yet.",
    })
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    payload = {"channel_id": "ch-scope", "thread_id": "main", "question_id": "q-fact",
               "resolution": "out_of_scope", "answer": "Test separately after PRD approval.",
               "resolved_by": "owner:operator"}
    return TestClient(create_app(state, project_root=tmp_path)), state, writer, payload


def test_scope_exclusion_is_durable_and_does_not_verify_fact(scope_case):
    client, state, _, payload = scope_case
    response = client.post("/api/actions/channel-question-resolve",
                           headers={"x-zf-web-token": "test-token"}, json=payload)
    assert response.status_code == 202, response.text
    reloaded = TestClient(create_app(state, project_root=state.parent))
    refreshed = reloaded.get("/api/channels/ch-scope")
    assert refreshed.status_code == 200, refreshed.text
    channel = refreshed.json()
    question = next(q for q in channel["open_questions"] if q["question_id"] == "q-fact")
    assert question["resolution"] == "out_of_scope"
    assert question["answer"] == payload["answer"]
    assert channel["cross_reviews"][0]["outcome"] == "unresolved"
    events = EventLog(state / "events.jsonl").read_all()
    assert len([e for e in events if e.type == "channel.question.resolved"]) == 1


@pytest.mark.parametrize("defect", ["missing_token", "empty_reason", "closed", "wrong_thread", "consolidating"])
def test_invalid_scope_exclusion_leaves_question_unchanged(scope_case, defect):
    client, state, writer, payload = scope_case
    headers = {"x-zf-web-token": "test-token"}
    if defect == "missing_token":
        headers = {}
    if defect == "empty_reason":
        payload["answer"] = "  "
    if defect == "wrong_thread":
        payload["thread_id"] = "other"
    if defect == "closed":
        writer.emit("channel.question.resolved", actor="web", payload=payload)
    if defect == "consolidating":
        writer.emit("channel.discussion.started", actor="web", payload={
            "channel_id": "ch-scope", "thread_id": "main", "state": "phase2_relay",
        })
        writer.emit("channel.discussion.phase.changed", actor="web", payload={
            "channel_id": "ch-scope", "thread_id": "main", "state": "phase2_relay",
        })
    before = project_channel(state, "ch-scope")["open_questions"]
    response = client.post("/api/actions/channel-question-resolve", headers=headers, json=payload)
    assert response.status_code >= 400, response.text
    assert project_channel(state, "ch-scope")["open_questions"] == before
