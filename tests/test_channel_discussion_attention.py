from __future__ import annotations

from zf.runtime.channel_discussion_attention import project_discussion_attention


def _channel() -> dict[str, object]:
    return {
        "discussions": {
            "main": {
                "state": "phase1_blind",
                "started_at": "2026-08-19T02:00:00Z",
                "roster": ["pm", "architect"],
            },
        },
        "consensus": {
            "main": {
                "ts": "2026-08-19T01:00:00Z",
                "artifact_ref": "channels/ch-test/prd/stale.json",
            },
        },
        "open_questions": {
            "question-1": {
                "question_id": "question-1",
                "thread_id": "main",
                "status": "open",
                "created_at": "2026-08-19T02:01:00Z",
            },
        },
        "synthesis_requests": [],
        "syntheses": [],
        "question_activity": [],
        "question_dedup_requests": [],
    }


def test_attention_keeps_active_execution_separate_from_owner_question() -> None:
    projection = project_discussion_attention(
        _channel(),
        [{
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "architect",
            "status": "running",
            "started_at": "2026-08-19T02:02:00Z",
        }],
        {"main": [{"question_id": "question-1"}]},
    )["main"]

    assert projection["schema_version"] == "channel.discussion-attention.v2"
    assert projection["state"] == "needs_input"
    assert projection["execution_state"] == "running"
    assert projection["attention_kind"] == "question"
    assert projection["blocking_scope"] == "phase"
    assert projection["blocks_transition"] == "synthesis"
    assert projection["active_agent_count"] == 1
    assert projection["can_review_result"] is False


def test_owner_question_becomes_waiting_gate_after_agents_finish() -> None:
    projection = project_discussion_attention(
        _channel(),
        [{
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "architect",
            "status": "completed",
            "started_at": "2026-08-19T02:02:00Z",
        }],
        {"main": [{"question_id": "question-1"}]},
    )["main"]

    assert projection["state"] == "needs_input"
    assert projection["execution_state"] == "ready"
    assert projection["attention_kind"] == "question"
    assert projection["active_agent_count"] == 0


def test_question_consolidation_hides_raw_owner_questions_until_applied() -> None:
    channel = _channel()
    channel["discussions"]["main"]["state"] = "phase2_relay"
    channel["question_dedup_requests"] = [{
        "thread_id": "main",
        "request_id": "dedup-1",
        "status": "requested",
        "ts": "2026-08-19T02:03:00Z",
    }]

    pending = project_discussion_attention(
        channel,
        [],
        {"main": [{"question_id": "question-1"}]},
    )["main"]

    assert pending["state"] == "running"
    assert pending["reason"] == "question_consolidation_active"
    assert pending["attention_kind"] == "consolidating"
    assert pending["owner_question_count"] == 0
    assert pending["can_review_questions"] is False

    channel["question_dedup_requests"][0]["status"] = "applied"
    channel["question_dedup_requests"][0]["updated_at"] = (
        "2026-08-19T02:04:00Z"
    )
    applied = project_discussion_attention(
        channel,
        [],
        {"main": [{"question_id": "question-1"}]},
    )["main"]

    assert applied["attention_kind"] == "question"
    assert applied["owner_question_count"] == 1
    assert applied["can_review_questions"] is True


def test_active_conversation_becomes_quiet_after_reply_cycle_settles() -> None:
    channel = _channel()
    channel["discussions"]["main"] = {
        "state": "active",
        "started_at": "2026-08-19T02:00:00Z",
        "roster": ["pm", "architect"],
    }
    channel["open_questions"] = {}
    channel["consensus"] = {}

    started = project_discussion_attention(
        channel,
        [],
        {"main": []},
    )["main"]
    assert started["state"] == "running"
    assert started["reason"] == "discussion_active"

    completed_reply = {
        "thread_id": "main",
        "request_id": "reply-1",
        "target_member_id": "architect",
        "status": "completed",
        "started_at": "2026-08-19T02:02:00Z",
    }
    settled = project_discussion_attention(
        channel,
        [completed_reply],
        {"main": []},
    )["main"]

    assert settled["kernel_phase"] == "active"
    assert settled["state"] == "done"
    assert settled["execution_state"] == "done"
    assert settled["reason"] == "discussion_idle"
    assert settled["next_action"] == "none"
    assert settled["active_reply_count"] == 0
    assert settled["completed_reply_count"] == 1
    assert settled["can_synthesize"] is True

    resumed = project_discussion_attention(
        channel,
        [
            completed_reply,
            {
                "thread_id": "main",
                "request_id": "reply-2",
                "target_member_id": "pm",
                "status": "queued",
                "started_at": "2026-08-19T02:03:00Z",
            },
        ],
        {"main": []},
    )["main"]
    assert resumed["state"] == "running"
    assert resumed["reason"] == "replies_active"
    assert resumed["active_agent_count"] == 1
    assert resumed["can_synthesize"] is False
