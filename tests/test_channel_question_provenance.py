"""Questions retain their originating reply through replay and Owner decisions."""
import json

import pytest
from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_graph import question_graph_digest
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.web.server import create_app


def seed_question(state, *, via_synthesis=True, channel_id="ch-source"):
    writer = EventWriter(EventLog(state / "events.jsonl"))

    def emit(kind, cause=None, **payload):
        return writer.emit(kind, actor="pm", causation_id=cause, payload={
            "channel_id": channel_id, "thread_id": "main", **payload,
        })

    emit("channel.created", name="Question provenance")
    emit("channel.member.invited", member_id="pm", member_type="provider_agent")
    for name in ("contribution", "synthesis"):
        emit("channel.message.posted", message_id=f"input-{name}", member_id="operator",
             role="user", text=f"Please run {name}")
        request = dict(request_id=f"reply-{name}", run_id=f"run-{name}",
                       message_id=f"input-{name}", target_member_id="pm")
        emit("channel.agent.reply.requested", **request)
        body = emit("channel.message.posted", message_id=f"body-{name}", member_id="pm",
                    role="assistant", text=f"Reply from {name}", refs=request)
        completed = emit("channel.agent.reply.completed", body.id, **request)
    cause = completed.id
    if via_synthesis:
        cause = emit("channel.synthesis.proposed", cause, request_id="synth-1").id
    question = emit("channel.question.opened", cause, question_id="q-owner",
                    question="Which retention clock applies?", asked_by="pm")
    return writer, question


@pytest.mark.parametrize("via_synthesis", [False, True])
def test_legacy_question_recovers_exact_reply_without_rewriting_ledger(tmp_path, via_synthesis):
    writer, _ = seed_question(tmp_path, via_synthesis=via_synthesis)
    before = writer.event_log.path.read_bytes()
    for _ in range(2):
        detail = project_channel(tmp_path, "ch-source")
        question = detail["open_questions"][0]
        assert question["source_run_id"] == "run-synthesis"
        assert question["source_message_id"] == "input-synthesis"
        assert detail["owner_questionnaires"]["main"][0]["source_run_id"] == "run-synthesis"
    assert writer.event_log.path.read_bytes() == before


@pytest.mark.parametrize("defect", ["channel", "thread", "member", "missing", "future", "cycle"])
def test_legacy_recovery_does_not_guess_across_invalid_causation(tmp_path, defect):
    writer, question = seed_question(tmp_path)
    events = writer.event_log.read_all()
    completed = next(e for e in events if e.type == "channel.agent.reply.completed"
                     and e.payload["run_id"] == "run-synthesis")
    if defect in {"channel", "thread", "member"}:
        key = {"channel": "channel_id", "thread": "thread_id", "member": "target_member_id"}[defect]
        completed.payload[key] = "different"
    elif defect == "missing":
        question.causation_id = "missing"
        events[-1] = question
    elif defect == "future":
        events.remove(completed)
        events.append(completed)
    else:
        proposed = next(e for e in events if e.type == "channel.synthesis.proposed")
        proposed.causation_id = proposed.id
    q = project_channel(tmp_path, "ch-source", events=events)["open_questions"][0]
    assert not q.get("source_run_id")
    assert not q.get("source_message_id")


def test_explicit_source_survives_update_and_resolve_without_rebinding(tmp_path):
    writer, question = seed_question(tmp_path)
    events = writer.event_log.read_all()
    events[-1].payload.update(source_run_id="unloaded-run", source_message_id="older-input")
    events.append(ZfEvent(type="channel.question.updated", actor="pm", payload={
        "channel_id": "ch-source", "question_id": "q-owner", "question": "Clarified question?",
        "source_run_id": "wrong-new-run", "source_message_id": "wrong-new-input",
    }))
    events.append(ZfEvent(type="channel.question.resolved", actor="owner", payload={
        "channel_id": "ch-source", "question_id": "q-owner", "resolution": "answered", "answer": "Event time",
    }))
    q = project_channel(tmp_path, "ch-source", events=events)["open_questions"][0]
    assert q["status"] == "resolved"
    assert q["source_run_id"] == "unloaded-run"
    assert q["source_message_id"] == "older-input"


def test_legacy_reply_without_run_id_matches_provider_projection(tmp_path):
    writer, _ = seed_question(tmp_path)
    events = writer.event_log.read_all()
    for event in events:
        event.payload.pop("run_id", None)
        (event.payload.get("refs") or {}).pop("run_id", None)
    detail = project_channel(tmp_path, "ch-source", events=events)
    run = next(r for r in detail["provider_runs"] if r["request_id"] == "reply-synthesis")
    assert detail["open_questions"][0]["source_run_id"] == run["run_id"]


@pytest.mark.parametrize("stage", ["contribution", "synthesis"])
def test_new_question_binds_runtime_identity_instead_of_model_fields(tmp_path, stage):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    channel = {"channel_id": "ch-source", "messages": [], "open_questions": [],
               "discussions": {"main": {"state": "phase1_blind", "requirement_message_id": "input-current"}}}
    request = {"request_id": "reply-current", "run_id": "run-current", "thread_id": "main",
               "message_id": "input-current", "target_member_id": "pm"}
    body = {"summary": "Owner must clarify retention.", "open_questions": [{
        "question": "Which clock applies?", "source_run_id": "model-invented",
        "source_message_id": "model-invented-input",
    }]}
    emit_structured_reply_events(
        state_dir=tmp_path, writer=writer, channel=channel, request=request,
        message={"refs": {"synthesis_request_id": "synth-1"} if stage == "synthesis" else {}},
        reply=json.dumps({f"channel_{stage}": body}), reply_event_id="completed-current",
        actor="test", source="test",
    )
    opened = next(e for e in writer.event_log.read_all() if e.type == "channel.question.opened")
    assert opened.payload["source_run_id"] == "run-current"
    assert opened.payload["source_message_id"] == "input-current"
    q = project_channel(tmp_path, "ch-source")["open_questions"][0]
    assert q["source_run_id"] == "run-current"


def test_provenance_enrichment_does_not_invalidate_question_graph_digest():
    question = {"question_id": "q", "thread_id": "main", "question": "Clock?", "status": "open"}
    before = question_graph_digest({"open_questions": [question]}, thread_id="main")
    enriched = {**question, "source_run_id": "run", "source_message_id": "input"}
    assert question_graph_digest({"open_questions": [enriched]}, thread_id="main") == before


def test_consensus_blocker_question_keeps_review_causation(tmp_path):
    from zf.runtime.channel_review_rework import open_unseen_consensus_blockers
    writer, _ = seed_question(tmp_path)
    completed = next(e for e in writer.event_log.read_all()
                     if e.type == "channel.agent.reply.completed" and e.payload["run_id"] == "run-synthesis")
    blocked = writer.emit("channel.consensus.blocked", actor="pm", causation_id=completed.id, payload={
        "channel_id": "ch-source", "thread_id": "main", "member_id": "pm",
        "blocker_question_id": "q-review", "blocker_question": "Which review scope?",
    })
    channel = project_channel(tmp_path, "ch-source")
    open_unseen_consensus_blockers(writer, channel, channel["consensus"]["main"], actor="channel-discussion",
                                   source="test", channel_id="ch-source", thread_id="main", synthesizer="pm")
    question = next(q for q in project_channel(tmp_path, "ch-source")["open_questions"]
                    if q["question_id"] == "q-review")
    assert question["source_run_id"] == "run-synthesis"
    opened = next(e for e in writer.event_log.read_all() if e.id == question["opened_event_id"])
    assert opened.causation_id == blocked.id


def test_api_answer_and_reload_preserve_recovered_source(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    seed_question(state)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "question-provenance-test")
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.post("/api/actions/channel-question-resolve",
                           headers={"x-zf-web-token": "question-provenance-test"}, json={
        "channel_id": "ch-source", "thread_id": "main", "question_id": "q-owner",
        "resolution": "answered", "answer": "Event time", "resolved_by": "owner:operator",
    })
    assert response.status_code == 202, response.text
    restarted = TestClient(create_app(state, project_root=tmp_path))
    response = restarted.get("/api/channels/ch-source/conversation")
    assert response.status_code == 200, response.text
    q = response.json()["open_questions"][0]
    assert q["answer"] == "Event time"
    assert q["source_run_id"] == "run-synthesis"
    assert q["source_message_id"] == "input-synthesis"
