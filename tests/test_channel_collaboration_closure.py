"""Owner deferral, replay and recovery through the real Channel entrypoints."""
from pathlib import Path

import pytest

from tests.test_channel_templates import _execute, _runtime
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_gate import question_dedup_gate_state

CH = "ch-collaboration"


def seed(tmp_path: Path):
    state, log, writer, service = _runtime(tmp_path)
    _execute(service, writer, "channel-create-from-template", {
        "template_id": "quick-change", "channel_id": CH, "overrides": {"backend": "fake"},
    })
    writer.emit("channel.discussion.started", actor="channel-discussion", payload={
        "channel_id": CH, "thread_id": "main", "roster": ["tech_leader", "dba"],
        "synthesizer": "tech_leader", "requirement_message_id": "requirement", "source": "test",
    })
    writer.emit("channel.discussion.phase.changed", actor="channel-discussion", payload={
        "channel_id": CH, "thread_id": "main", "phase": "phase2_relay",
        "reason": "synthesis_questions_opened", "source": "test",
    })
    writer.emit("channel.question.opened", actor="tech_leader", payload={
        "channel_id": CH, "thread_id": "main", "question_id": "q-db", "question": "Choose databases?",
        "kind": "owner_decision", "asked_by": "tech_leader", "source": "test",
    })
    return state, log, writer, service


def defer(service, writer, **extra):
    return _execute(service, writer, "channel-question-resolve", {
        "channel_id": CH, "thread_id": "main", "question_id": "q-db", "resolution": "deferred",
        "answer": "本轮暂缓；不删除原始需求，也不批准推荐方案。", **extra,
    })


def test_owner_can_defer_synthesis_question_and_replay_preserves_decision(tmp_path):
    state, log, writer, service = seed(tmp_path)
    assert defer(service, writer)["ok"]
    question = project_channel(state, CH)["open_questions"][0]
    assert question["resolution"] == "deferred" and question["status"] == "resolved"
    assert not defer(service, writer)["ok"]
    # Old callbacks and a mismatched thread must not replace the first decision.
    for thread in ("main", "foreign"):
        writer.emit("channel.question.resolved", actor="web", payload={
            "channel_id": CH, "thread_id": thread, "question_id": "q-db",
            "resolution": "answered", "answer": "Choose both", "resolved_by": "owner:operator",
        })
    assert project_channel(state, CH)["open_questions"][0] == question
    assert not any(e.type in {"channel.consensus.reached", "task.created", "workflow.started"} for e in log.read_all())


def test_agent_cannot_defer_or_spoof_owner_through_controlled_action(tmp_path):
    state, _, writer, service = seed(tmp_path)
    assert not defer(service, writer, resolved_by="tech_leader")["ok"]
    writer.emit("channel.question.resolved", actor="tech_leader", payload={
        "channel_id": CH, "thread_id": "main", "question_id": "q-db", "resolution": "deferred",
        "answer": "skip", "resolved_by": "owner:operator",
    })
    assert project_channel(state, CH)["open_questions"][0]["status"] == "open"


def test_deferral_requires_explicit_reason(tmp_path):
    _, _, writer, service = seed(tmp_path)
    assert not defer(service, writer, answer="")["ok"]


def test_deferred_ledger_survives_context_and_advances_once_only_when_roles_complete(tmp_path):
    state, log, writer, service = seed(tmp_path)
    assert defer(service, writer)["ok"]
    writer.emit("channel.questions.frozen", actor="tech_leader", payload={
        "channel_id": CH, "thread_id": "main", "member_id": "tech_leader", "source": "test",
    })
    assert "channel.synthesis.requested" not in advance_discussion(state, writer, channel_id=CH, thread_id="main")
    attention = project_channel(state, CH)["discussion_attention"]["main"]
    assert attention["missing_frozen_members"] == ["dba"]
    assert attention["reason"] == "contribution_missing" and attention["can_restart"]
    writer.emit("channel.questions.frozen", actor="dba", payload={
        "channel_id": CH, "thread_id": "main", "member_id": "dba", "source": "test",
    })
    assert "channel.synthesis.requested" in advance_discussion(state, writer, channel_id=CH, thread_id="main")
    advance_discussion(state, writer, channel_id=CH, thread_id="main")
    assert sum(e.type == "channel.synthesis.requested" for e in log.read_all()) == 1
    writer.emit("channel.message.posted", actor="web", payload={
        "channel_id": CH, "thread_id": "main", "message_id": "synth-msg", "text": "synthesize",
        "refs": {"synthesis_request_id": "synth-1"},
    })
    pack = build_channel_context_pack(project_channel(state, CH), channel_id=CH, thread_id="main",
        target_member_id="tech_leader", trigger_message_id="synth-msg")
    assert pack["question_ledger"][0]["resolution"] == "deferred"
    assert pack["question_ledger"][0]["resolved_event_id"]


@pytest.mark.parametrize("status,expected", [("requested", "consolidating"), ("rejected", "consolidating"), ("exhausted", "blocked")])
def test_synthesis_rollback_does_not_bypass_actual_dedup_request(status, expected):
    channel = {"open_questions": [{"thread_id": "main", "status": "open"}],
        "discussions": {"main": {"state": "phase2_relay", "phase_reason": "synthesis_questions_opened"}},
        "question_dedup_requests": [{"thread_id": "main", "status": status}]}
    assert question_dedup_gate_state(channel, thread_id="main") == expected


def test_missing_blind_dedup_is_repaired_after_restart_without_duplicate_requests(tmp_path):
    state, log, writer, _ = seed(tmp_path)
    writer.emit("channel.discussion.phase.changed", actor="channel-discussion", payload={
        "channel_id": CH, "thread_id": "main", "phase": "phase2_relay",
        "reason": "blind_round_complete", "source": "test",
    })
    assert question_dedup_gate_state(project_channel(state, CH), thread_id="main") == "consolidating"
    assert advance_discussion(state, writer, channel_id=CH, thread_id="main") == ["channel.question.dedup.requested"]
    advance_discussion(state, writer, channel_id=CH, thread_id="main")
    assert sum(e.type == "channel.question.dedup.requested" for e in log.read_all()) == 1


def test_duplicate_question_in_synthesis_does_not_strand_review_after_owner_deferral(tmp_path):
    import json
    from zf.runtime.channel_reply_contract import emit_structured_reply_events

    state, log, writer, service = seed(tmp_path)
    assert defer(service, writer)["ok"]
    emit_structured_reply_events(
        state_dir=state, writer=writer, channel=project_channel(state, CH),
        request={"thread_id": "main", "target_member_id": "tech_leader"},
        message={"refs": {"synthesis_request_id": "synth-duplicate"}},
        reply=json.dumps({"channel_synthesis": {"title": "Draft", "summary": "Database decision remains deferred",
            "decisions": ["Keep original requirements"], "open_questions": ["Choose databases?"],
            "readiness": {"verdict": "needs_owner", "implementation_start": False,
                "gaps": ["Database choice deferred"], "reason": "Owner deferred"}}}),
        reply_event_id="reply-duplicate", actor="tech_leader", source="test",
    )
    channel = project_channel(state, CH)
    assert len(channel["open_questions"]) == 1
    assert channel["open_questions"][0]["resolution"] == "deferred"
    proposal = next(event for event in log.read_all() if event.type == "channel.consensus.proposed")
    synthesis = next(event for event in log.read_all() if event.type == "channel.synthesis.proposed")
    assert proposal.payload["artifact_digest"] == synthesis.payload["artifact_digest"]
    assert not channel["consensus"]["main"].get("human_confirmed")
