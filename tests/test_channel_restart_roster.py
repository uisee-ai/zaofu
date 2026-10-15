"""Restart must freeze the current roster without losing Owner decisions."""
import pytest

from tests.test_channel_collaboration_closure import CH, seed, defer
from tests.test_channel_templates import _execute
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_projection import project_channel


@pytest.mark.parametrize("remove_member", [False, True])
def test_restart_uses_current_roster_and_preserves_owner_decision(tmp_path, remove_member):
    state, log, writer, service = seed(tmp_path)
    assert defer(service, writer)["ok"]
    decision = project_channel(state, CH)["open_questions"][0]
    assert _execute(service, writer, "channel-invite-member", {
        "channel_id": CH, "member_id": "dba", "backend": "fake",
        "member_type": "provider_agent", "channel_role": "arch",
    })["ok"]
    roster = ["tech_leader", "dev_reviewer", "qa_analyst", "dba"]
    if remove_member:
        assert _execute(service, writer, "channel-remove-member", {
            "channel_id": CH, "member_id": "qa_analyst",
        })["ok"]
        roster.remove("qa_analyst")
    assert _execute(service, writer, "channel-discussion-mode", {
        "channel_id": CH, "mode": "multi_lens", "participants": roster,
        "synthesizer": "tech_leader", "default_responder_id": "tech_leader",
    })["ok"]
    result = _execute(service, writer, "channel-discussion-start", {
        "channel_id": CH, "thread_id": "main", "mode": "multi_lens",
        "message": "Review the complete requirement with all current members.",
        "restart": True, "dispatch_policy": "trigger_only",
    })
    assert result["ok"], result
    channel = project_channel(state, CH)
    assert channel["discussions"]["main"]["participants"] == roster
    assert channel["discussions"]["main"]["review_roster"] == roster
    assert channel["open_questions"][0] == decision
    assert set(result["route"]["targets"]) == set(roster)
    advance_discussion(state, writer, channel_id=CH, thread_id="main")
    assert project_channel(state, CH)["discussions"]["main"]["state"] == "phase1_blind"
    assert not any(e.type == "channel.synthesis.requested" for e in log.read_all())


def test_next_round_rejection_reports_existing_prd_and_ignores_old_discussion():
    from tests.test_channel_discussion_attention import _channel
    from zf.runtime.channel_discussion_attention import project_discussion_attention

    channel = _channel()
    channel["open_questions"] = {}
    channel["consensus"] = {}
    channel["discussions"]["main"].update(state="phase3_synthesis", discussion_id="new", revision=1)
    channel["syntheses"] = [{"thread_id": "main", "event_id": "synth-new", "artifact_ref": "prd/r1.json",
                             "ts": "2026-08-19T02:05:00Z"}]
    rejection = {"thread_id": "main", "type": "channel.discussion.next_round.rejected",
                 "discussion_id": "new", "expected_revision": 1, "synthesis_event_id": "synth-new",
                 "reason": "next_round_targets_not_in_current_roster"}
    channel["next_rounds"] = [rejection]
    attention = project_discussion_attention(channel, [], {})["main"]
    assert attention["reason"] == "next_round_rejected"
    assert attention["next_round_rejection_reason"] == rejection["reason"]
    assert attention["execution_state"] == "blocked"
    assert attention["can_restart"] and not attention["can_synthesize"]
    rejection["discussion_id"] = "old"
    attention = project_discussion_attention(channel, [], {})["main"]
    assert attention["reason"] == "synthesis_review_pending"
    assert not attention["next_round_rejection_reason"]


def test_next_round_rejection_is_consumed_from_event_log(tmp_path):
    state, _, writer, service = seed(tmp_path)
    assert defer(service, writer)["ok"]
    session = project_channel(state, CH)["discussions"]["main"]
    writer.emit("channel.discussion.phase.changed", actor="kernel", payload={
        "channel_id": CH, "thread_id": "main", "phase": "phase3_synthesis"})
    synthesis = writer.emit("channel.synthesis.proposed", actor="tech_leader", payload={
        "channel_id": CH, "thread_id": "main", "artifact_ref": "prd/r1.json", "artifact_digest": "digest"})
    writer.emit("channel.discussion.next_round.rejected", actor="kernel", payload={
        "channel_id": CH, "thread_id": "main", "discussion_id": session["discussion_id"],
        "expected_revision": session["revision"], "synthesis_event_id": synthesis.id,
        "reason": "next_round_targets_not_in_current_roster"})
    attention = project_channel(state, CH)["discussion_attention"]["main"]
    assert attention["reason"] == "next_round_rejected"
    assert attention["next_round_rejection_reason"] == "next_round_targets_not_in_current_roster"
