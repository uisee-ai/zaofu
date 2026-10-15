from __future__ import annotations

from zf.runtime.channel_discussion_attention import (
    project_discussion_attention,
    project_discussion_evaluation_summary,
)


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


def test_context_rejection_blocks_even_without_a_provider_request_and_clears_after_rebuild():
    channel = _channel()
    channel["discussions"]["main"]["state"] = "phase2_relay"
    channel["context_packs"] = {"ctx": {"context_pack_id": "ctx", "thread_id": "main", "status": "rejected",
        "target_member_id": "architect", "reason": "semantic_source_budget_exceeded:121844>120000", "ts": "2026-08-19T02:04:00Z"}}
    attention = project_discussion_attention(channel, [], {})["main"]
    assert attention["state"] == "blocked"
    assert attention["execution_state"] == "blocked"
    assert attention["reason"] == "context_pack_rejected"
    assert attention["context_rejections"][0]["target_member_id"] == "architect"
    assert not attention["can_synthesize"]
    channel["context_packs"]["ctx"]["status"] = "built"
    assert not project_discussion_attention(channel, [], {})["main"]["context_rejections"]


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
    assert resumed["reason"] == "replies_queued"
    assert resumed["execution_state"] == "queued"
    assert resumed["active_agent_count"] == 0
    assert resumed["queued_reply_count"] == 1
    assert resumed["can_drain_replies"] is True
    assert resumed["can_synthesize"] is False


def test_multi_lens_exposes_role_coverage_and_gates_synthesis() -> None:
    channel = _channel()
    channel["discussions"]["main"] = {
        "state": "phase2_relay",
        "product_mode": "multi_lens",
        "started_at": "2026-08-19T02:00:00Z",
        "requirement_message_id": "requirement-1",
        "roster": ["pm", "architect", "critic"],
        "synthesizer": "pm",
    }
    channel["discussion"] = {"mode": "multi_lens"}
    channel["members"] = {
        "pm": {"member_id": "pm", "channel_role": "product_pm", "display_name": "PM"},
        "architect": {"member_id": "architect", "channel_role": "architect", "display_name": "Architect"},
        "critic": {"member_id": "critic", "channel_role": "critic", "display_name": "Critic"},
    }
    channel["contributions"] = {
        "main:pm": {
            "event_id": "contribution-pm",
            "thread_id": "main",
            "member_id": "pm",
            "message_id": "requirement-1",
            "request_id": "reply-pm",
            "contract_status": "structured",
            "ts": "2026-08-19T02:03:00Z",
        },
    }
    channel["open_questions"] = {}
    channel["consensus"] = {}
    projection = project_discussion_attention(
        channel,
        [
            {
                "thread_id": "main",
                "request_id": "reply-pm",
                "message_id": "requirement-1",
                "target_member_id": "pm",
                "status": "completed",
                "updated_at": "2026-08-19T02:03:00Z",
            },
            {
                "thread_id": "main",
                "request_id": "reply-architect",
                "message_id": "requirement-1",
                "target_member_id": "architect",
                "status": "running",
                "updated_at": "2026-08-19T02:04:00Z",
            },
        ],
        {"main": []},
    )["main"]

    assert projection["coverage_required"] is True
    assert projection["coverage_complete"] is False
    assert projection["coverage_summary"] == {
        "required": 3,
        "total": 3,
        "completed": 1,
        "required_completed": 1,
        "responded": 0,
        "pending": 1,
        "queued": 0,
        "running": 1,
        "failed": 0,
        "skipped": 0,
    }
    assert projection["coverage_missing"] == ["architect", "critic"]
    assert projection["can_synthesize"] is False
    assert projection["discussion_contract"]["synthesizer"] == "pm"
    assert projection["role_coverage"][1]["status"] == "running"


def test_multi_lens_all_structured_roles_unlock_synthesis() -> None:
    channel = _channel()
    channel["discussions"]["main"] = {
        "state": "phase2_relay",
        "product_mode": "multi_lens",
        "started_at": "2026-08-19T02:00:00Z",
        "requirement_message_id": "requirement-1",
        "roster": ["pm", "architect"],
        "synthesizer": "pm",
    }
    channel["discussion"] = {"mode": "multi_lens"}
    channel["members"] = {}
    channel["contributions"] = {
        f"main:{member}": {
            "event_id": f"contribution-{member}",
            "thread_id": "main",
            "member_id": member,
            "message_id": "requirement-1",
            "request_id": f"reply-{member}",
            "contract_status": "structured",
            "ts": f"2026-08-19T02:0{index + 3}:00Z",
        }
        for index, member in enumerate(("pm", "architect"))
    }
    channel["questions_frozen"] = {"main": {"pm": "2026-08-19T02:03:00Z", "architect": "2026-08-19T02:04:00Z"}}
    channel["open_questions"] = {}
    channel["consensus"] = {}
    projection = project_discussion_attention(
        channel,
        [
            {
                "thread_id": "main",
                "request_id": f"reply-{member}",
                "message_id": "requirement-1",
                "target_member_id": member,
                "status": "completed",
                "updated_at": f"2026-08-19T02:0{index + 3}:00Z",
            }
            for index, member in enumerate(("pm", "architect"))
        ],
        {"main": []},
    )["main"]

    assert projection["coverage_complete"] is True
    assert projection["coverage_missing"] == []
    assert projection["can_synthesize"] is True


def test_attention_surfaces_provider_incomplete_output_provenance() -> None:
    channel = _channel()
    channel["discussions"]["main"] = {
        "state": "active",
        "started_at": "2026-08-19T02:00:00Z",
        "roster": ["architect"],
    }
    channel["open_questions"] = {}
    channel["consensus"] = {}
    projection = project_discussion_attention(
        channel,
        [{
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "architect",
            "status": "failed",
            "completion_reason": "max_tokens",
            "updated_at": "2026-08-19T02:02:00Z",
        }],
        {"main": []},
    )["main"]

    assert projection["answer_integrity"] == {
        "incomplete_reply_count": 1,
        "incomplete_reply_request_ids": ["reply-1"],
        "full_body_source": "channel.message.body_ref when present; conversation projection hydrates it",
    }


def test_evaluation_summary_is_comparable_and_preserves_p0_evidence() -> None:
    channel = _channel()
    channel["discussion"] = {"mode": "multi_lens", "engine_mode": "fanout_then_synthesis"}
    channel["discussions"]["main"] = {
        "state": "phase3_synthesis",
        "product_mode": "multi_lens",
        "roster": ["pm"],
    }
    channel["members"] = {
        "pm": {"member_id": "pm", "channel_role": "product_pm"},
    }
    channel["contributions"] = {
        "main:pm": {
            "thread_id": "main",
            "member_id": "pm",
            "findings": [{"severity": "P0", "summary": "delete is not atomic"}],
            "risks": [{"severity": "high", "summary": "rollback is unclear"}],
            "evidence_refs": ["prd:7.4"],
        },
    }
    channel["open_questions"] = {}
    attention = project_discussion_attention(
        channel,
        [{
            "thread_id": "main",
            "request_id": "reply-pm",
            "target_member_id": "pm",
            "status": "completed",
        }],
        {"main": []},
    )

    summary = project_discussion_evaluation_summary(channel, attention)

    assert summary["schema_version"] == "channel.evaluation-summary.v1"
    assert summary["mode"] == "multi_lens"
    assert summary["engine_mode"] == "fanout_then_synthesis"
    assert summary["p0_findings"][0]["summary"] == "delete is not atomic"
    assert summary["p1_findings"][0]["summary"] == "rollback is unclear"
    assert summary["evidence_refs"] == ["prd:7.4"]
    assert summary["threads"]["main"]["role_coverage"]


def test_evaluation_summary_handles_string_and_contract_finding_shapes() -> None:
    channel = _channel()
    channel["discussion"] = {"mode": "multi_lens"}
    channel["discussions"]["main"] = {
        "state": "phase2_relay",
        "product_mode": "multi_lens",
        "roster": ["critic"],
    }
    channel["members"] = {
        "critic": {"member_id": "critic", "channel_role": "critic"},
    }
    channel["contributions"] = {
        "main:critic": {
            "thread_id": "main",
            "member_id": "critic",
            "findings": [{"severity": "p0", "finding": "target write is missing"},
                         {"priority": "p1", "text": "new reviewer was not admitted"}],
            "risks": ["inventory rollback is unspecified"],
            "evidence_refs": ["PRD.md:6.2"],
        },
    }
    channel["open_questions"] = {}
    attention = project_discussion_attention(channel, [], {"main": []})
    summary = project_discussion_evaluation_summary(channel, attention)

    assert [row["summary"] for row in summary["p0_findings"]] == [
        "target write is missing",
    ]
    assert summary["p1_findings"][0]["summary"] == "new reviewer was not admitted"


def test_attention_distinguishes_merged_from_answered_questions() -> None:
    channel = _channel()
    channel["open_questions"]["merged"] = {"status": "merged", "thread_id": "main"}
    channel["open_questions"]["answered"] = {"status": "resolved", "thread_id": "main", "answer": "Approved"}
    attention = project_discussion_attention(channel, [], {})["main"]
    assert attention["resolved_question_count"] == 1
    assert attention["merged_question_count"] == 1


def test_adaptive_attention_does_not_offer_old_synthesis_as_current_result() -> None:
    channel = _channel()
    channel["open_questions"] = {}
    channel["discussions"]["main"].update(state="phase3_synthesis", continued_at="2026-08-19T03:00:00Z")
    channel["syntheses"] = [{"thread_id": "main", "ts": "2026-08-19T02:30:00Z"}]
    channel["consensus"]["main"]["ts"] = "2026-08-19T02:30:00Z"
    channel["synthesis_requests"] = [{"thread_id": "main", "ts": "2026-08-19T03:01:00Z", "status": "stale_ignored"}]
    attention = project_discussion_attention(channel, [], {})["main"]
    assert attention["state"] == "blocked"
    assert attention["reason"] == "synthesis_superseded"
    assert attention["can_restart"] and not attention["can_review_result"]


def test_review_blocker_after_stall_is_not_presented_as_owner_decision() -> None:
    channel = _channel()
    channel["open_questions"] = {}
    channel["discussions"]["main"].update(
        state="idle", last_outcome="stalled", closed_at="2026-08-19T03:00:00Z",
    )
    channel["consensus"]["main"].update(
        ts="2026-08-19T02:30:00Z",
        artifact_ref="channels/ch-test/prd/r3.json",
        blocked=[{"member_id": "critic", "blocker_kind": "revision_request"}],
    )
    channel["syntheses"] = [{"thread_id": "main", "ts": "2026-08-19T02:30:00Z"}]
    attention = project_discussion_attention(channel, [], {})["main"]
    assert attention["state"] == "blocked"
    assert attention["reason"] == "discussion_stalled"
    assert attention["attention_kind"] == "none"
    assert attention["owner_question_count"] == 0
    assert not attention["can_review_result"]
def test_closed_discussion_evaluation_summary_is_done():
    from zf.runtime.channel_discussion_attention import project_discussion_evaluation_summary

    summary = project_discussion_evaluation_summary(
        {"discussions": {"main": {"state": "idle", "last_outcome": "consensus_reached"}}},
        {"main": {"state": "done", "can_synthesize": False}},
    )
    assert summary["threads"]["main"]["result_status"] == "done"
