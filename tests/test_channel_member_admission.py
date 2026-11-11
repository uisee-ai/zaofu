"""Leader contribution -> Owner decision -> formal reviewer through real callers."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from zf.core.config.schema import ChannelAgentProfileConfig, ChannelConfig, ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_adapter import dispatch_pending_replies
from zf.runtime.channel_discussion import start_discussion
from zf.runtime.channel_prd_revision import consensus_mode_and_required_signers
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_provider_prompt import build_channel_provider_prompt
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_sidecar import channel_message_event_payload, hydrate_channel_context_pack_payload
from zf.runtime.control_actions import ControlledActionService


def execute(service, writer, action, payload):
    request = writer.emit("web.action.requested", actor=service.actor, payload={"action": action})
    return service.execute(action=action, requested_action=action, payload=payload, requested=request)


def fixture(tmp_path: Path, monkeypatch, *, role_context_ref="", mode="multi_lens"):
    state = tmp_path / "state"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    config = ZfConfig(project=ProjectConfig(name="admission"), channel=ChannelConfig(agent_profiles={
        "qa-audit": ChannelAgentProfileConfig(persona="QA reviewer", channel_role="qa_analyst",
                                               provider="fake", backend="fake", permission_ceiling="read_only",
                                               role_context_ref=role_context_ref),
    }))
    service = ControlledActionService(state, writer, project_root=tmp_path, actor="web", config=config)
    created = execute(service, writer, "channel-create-from-template", {
        "channel_id": "ch-admit", "template_id": "prd-clarification", "name": "Admission",
        "mode": mode, "overrides": {"backend": "fake"},
    })
    assert created["ok"], created
    body = channel_message_event_payload(state, {"channel_id": "ch-admit", "thread_id": "main",
        "message_id": "brief", "member_id": "operator", "role": "user", "mentions": ["all"], "text": "@all Review inventory atomicity."}, created_by="web")
    message = writer.emit("channel.message.posted", actor="web", payload=body)
    channel = project_channel(state, "ch-admit")
    if mode != "libre_discuss":
        start_discussion(writer, channel, actor="web", channel_id="ch-admit", thread_id="main",
                         trigger_message_id="brief", trigger="explicit", source="test")
    route_channel_message(state_dir=state, writer=writer, message_event=message, message_payload=body,
                          actor="web", source="test", project_root=tmp_path, config=config, dispatch_inline=False)
    channel = project_channel(state, "ch-admit")
    request = next(r for r in channel["reply_requests"] if r["target_member_id"] == channel["leader_member_id"])
    monkeypatch.setattr("zf.runtime.channel_message_ingress.dispatch_channel_replies_background", lambda **kwargs: None)
    return state, writer, config, service, channel, request


def propose(state, writer, channel, request, *, profile="qa-audit"):
    contribution = {"summary": "Independent QA is needed", "questions": [], "freeze": True,
                    "member_invitations": [{"member_id": "qa", "profile_id": profile,
                                            "reason": "Verify atomic inventory counterexamples", "source_refs": ["brief"],
                                            "required_reviewer": True}]}
    emit_structured_reply_events(state_dir=state, writer=writer, channel=channel, request=request,
                                 message={"message_id": "brief"}, reply=json.dumps({"channel_contribution": contribution}),
                                 reply_event_id="evt-leader-reply", actor=channel["leader_member_id"], source="test")
    return project_channel(state, "ch-admit")


def test_leader_invitation_enters_formal_round_and_keeps_final_reviewers(tmp_path, monkeypatch):
    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch)
    prompt = build_channel_provider_prompt(channel=channel, request=request,
        member={"member_id": "product_pm"}, message={"text": "Review"}, state_dir=state)
    assert "qa-audit" in prompt and "current_channel_group" in prompt
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    assert channel["discussion_attention"]["main"]["reason"] == "member_invitation_pending"
    assert not channel["discussion_attention"]["main"]["can_synthesize"]
    payload = {"channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]}
    result = execute(service, writer, "channel-invite-member", payload)
    assert result["ok"], result
    events = writer.event_log.read_all()
    decision = next(e for e in events if e.type == "channel.member.invitation.decided")
    admission = next(e for e in events if e.type == "channel.message.posted"
                     and e.payload.get("message_id") == f"msg-admission-{proposal['proposal_id']}")
    packs = [e for e in events if e.type == "channel.context_pack.built"
             and e.payload.get("trigger_message_id") == admission.payload["message_id"]]
    assert packs
    assert events.index(decision) < events.index(packs[0])
    for pack in packs:
        frozen = hydrate_channel_context_pack_payload(state, pack.payload, strict=True)
        assert frozen["current_channel_group"]["member_invitation_decisions"][0]["status"] == "approved"
        assert frozen["current_channel_group"]["member_invitation_decisions"][0]["decision_event_id"] == decision.id
    again = execute(service, writer, "channel-invite-member", payload)
    assert again["status"] == "already_decided", again
    channel = project_channel(state, "ch-admit")
    qa = next(m for m in channel["members"] if m["member_id"] == "qa")
    assert qa["permission_profile"] == "read_only"
    session = channel["discussions"]["main"]
    assert "qa" in session["roster"] and "qa" in session["review_roster"]
    dispatch_pending_replies(state_dir=state, writer=writer, channel_id="ch-admit", actor="test", source="test",
                             max_dispatch=20, allow_queued=True, project_root=tmp_path, config=config)
    channel = project_channel(state, "ch-admit")
    assert any(c["member_id"] == "qa" and c.get("artifact_ref") for c in channel["contributions"])
    writer.emit("channel.discussion.continued", actor="test", payload={
        "channel_id": "ch-admit", "thread_id": "main", "roster": ["product_pm"], "revision": 3})
    _, signers = consensus_mode_and_required_signers(project_channel(state, "ch-admit"), thread_id="main")
    assert {"product_pm", "arch", "critic", "qa"} <= set(signers)
    assert sum(e.type == "channel.member.invited" and e.payload.get("member_id") == "qa" for e in writer.event_log.read_all()) == 1


def test_formal_review_sources_include_scoped_approval_occurrence(tmp_path, monkeypatch):
    from zf.runtime.channel_context import build_channel_context_pack

    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch)
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    assert execute(service, writer, "channel-invite-member", {
        "channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"],
    })["ok"]
    for channel_id, thread_id in (("other-channel", "main"), ("ch-admit", "other-thread")):
        for event_type in ("channel.member.invitation.proposed", "channel.member.invitation.decided", "channel.member.invited"):
            writer.emit(event_type, actor="web", correlation_id=channel_id, payload={
                "channel_id": channel_id, "thread_id": thread_id, "member_id": "foreign", "decision": "approved"})
    writer.emit("channel.message.posted", actor="web", correlation_id="ch-admit", payload=channel_message_event_payload(
        state, {"channel_id": "ch-admit", "thread_id": "main", "message_id": "review-admission",
                "member_id": "operator", "role": "user", "text": "Verify admission evidence.",
                "refs": {"cross_review_request_id": "review-admission"}}, created_by="test"))
    frozen = build_channel_context_pack(project_channel(state, "ch-admit"), channel_id="ch-admit", thread_id="main",
                                        target_member_id="qa", trigger_message_id="review-admission", state_dir=state,
                                        project_root=tmp_path, config=config)
    assert frozen["semantic_source_complete"]
    receipts = [row for row in frozen["semantic_source_documents"] if row["kind"] == "member_invitation_decision"]
    assert len(receipts) == 1
    assert receipts[0]["content"]["actor"] == "web"
    assert receipts[0]["content"]["payload"]["decision"] == "approved"
    assert receipts[0]["content"]["payload"]["proposal_id"] == proposal["proposal_id"]
    for kind, event_type, proposal_key in (
        ("member_invitation_proposal", "channel.member.invitation.proposed", "proposal_id"),
        ("member_admission", "channel.member.invited", "invitation_proposal_id"),
    ):
        all_bodies = [row["content"] for row in frozen["semantic_source_documents"] if row["kind"] == kind]
        assert all(body["payload"]["member_id"] != "foreign" for body in all_bodies)
        bodies = [body for body in all_bodies if body["payload"].get(proposal_key) == proposal["proposal_id"]]
        assert len(bodies) == 1
        assert bodies[0]["type"] == event_type
        assert bodies[0]["payload"][proposal_key] == proposal["proposal_id"]
        assert bodies[0]["payload"]["profile_id"] == proposal["profile_id"]


@pytest.mark.parametrize("fault", ["actor", "catalog", "evidence", "target", "decline"])
def test_invitation_boundaries(tmp_path, monkeypatch, fault):
    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch)
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    payload = {"channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]}
    if fault == "actor": service.actor = "stranger"
    if fault == "catalog": config.channel.agent_profiles["qa-audit"] = replace(config.channel.agent_profiles["qa-audit"], revision=2)
    if fault == "evidence": (state / proposal["artifact_ref"]).write_text("{}")
    if fault == "target": payload["member_id"] = "other"
    if fault == "decline": payload["decision"] = "rejected"
    result = execute(service, writer, "channel-invite-member", payload)
    assert result["ok"] is (fault == "decline"), result
    assert not any(m["member_id"] == "qa" for m in project_channel(state, "ch-admit")["members"])


def test_unknown_profile_does_not_create_an_invitation(tmp_path, monkeypatch):
    state, writer, _, _, channel, request = fixture(tmp_path, monkeypatch)
    channel = propose(state, writer, channel, request, profile="invented")
    assert channel["member_invitation_proposals"] == []
    assert any(e.type == "channel.agent.reply.failed" for e in writer.event_log.read_all())



def test_rejected_invitation_can_retry_after_missing_role_definition_is_restored(tmp_path, monkeypatch):
    state, writer, config, service, channel, request = fixture(
        tmp_path, monkeypatch, role_context_ref="channel_roles/qa-analyst.md")
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    payload = {"channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]}
    result = execute(service, writer, "channel-invite-member", payload)
    assert not result["ok"]
    assert "definition is missing" in result["reason"]
    rejected = project_channel(state, "ch-admit")
    assert next(m for m in rejected["members"] if m["member_id"] == "qa")["status"] == "rejected"
    (tmp_path / "channel_roles").mkdir()
    (tmp_path / "channel_roles/qa-analyst.md").write_text("# QA reviewer\nReview negative cases and evidence.\n")
    result = execute(service, writer, "channel-invite-member", payload)
    assert result["ok"], result
    admitted = project_channel(state, "ch-admit")
    qa = next(m for m in admitted["members"] if m["member_id"] == "qa")
    assert qa["status"] == "invited"
    assert not qa["last_error"]
    assert "qa" in admitted["discussions"]["main"]["review_roster"]
    assert admitted["member_invitation_proposals"][0]["status"] == "approved"
    assert execute(service, writer, "channel-invite-member", payload)["status"] == "already_decided"
    dispatch_pending_replies(state_dir=state, writer=writer, channel_id="ch-admit", actor="test", source="test",
                             max_dispatch=20, allow_queued=True, project_root=tmp_path, config=config)
    admitted = project_channel(state, "ch-admit")
    assert any(c["member_id"] == "qa" and c.get("artifact_ref") for c in admitted["contributions"])


def test_invitation_cannot_overwrite_previously_admitted_identity_after_rejection(tmp_path, monkeypatch):
    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch)
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    writer.emit("channel.member.invited", correlation_id="ch-admit", payload={
        "channel_id": "ch-admit", "member_id": "qa", "profile_id": "different-profile"})
    writer.emit("channel.member.add.rejected", correlation_id="ch-admit", payload={
        "channel_id": "ch-admit", "member_id": "qa", "reason": "failed later rebind"})
    result = execute(service, writer, "channel-invite-member", {
        "channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]})
    assert not result["ok"]
    assert result["reason"] == "invitation member_id is already in use"

def test_late_pre_admission_reply_cannot_replace_current_contribution(tmp_path, monkeypatch):
    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch)
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    result = execute(service, writer, "channel-invite-member", {
        "channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]})
    assert result["ok"]
    before = project_channel(state, "ch-admit")["contributions"]
    for _ in range(2):
        emit_structured_reply_events(state_dir=state, writer=writer, channel=project_channel(state, "ch-admit"),
            request=request, message={"refs": {"adaptive_next_round_id": "old-pass"}},
            reply=json.dumps({"channel_contribution": {"summary": "late stale conclusion", "questions": [], "freeze": True}}),
            reply_event_id="late-before-admission", actor=channel["leader_member_id"], source="test")
    assert project_channel(state, "ch-admit")["contributions"] == before
    assert sum(e.type == "channel.contribution.stale_ignored" for e in writer.event_log.read_all()) == 1


def test_invitation_recovers_after_invited_event_without_duplicate_member(tmp_path, monkeypatch):
    import zf.runtime.channel_member_admission as admission
    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch)
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    payload = {"channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]}
    original = admission.complete_member_admission
    def interrupted(*args, **kwargs):
        raise RuntimeError("simulated interruption after member event")
    monkeypatch.setattr(admission, "complete_member_admission", interrupted)
    try:
        first = execute(service, writer, "channel-invite-member", payload)
        assert not first["ok"]
    except RuntimeError as exc:
        assert "simulated interruption" in str(exc)
    monkeypatch.setattr(admission, "complete_member_admission", original)
    result = execute(service, writer, "channel-invite-member", payload)
    assert result["ok"], result
    events = writer.event_log.read_all()
    assert sum(e.type == "channel.member.invited" and e.payload.get("member_id") == "qa" for e in events) == 1
    assert sum(e.type == "channel.member.invitation.decided" and e.payload.get("proposal_id") == proposal["proposal_id"] for e in events) == 1
    assert "qa" in project_channel(state, "ch-admit")["discussions"]["main"]["review_roster"]


def test_approved_invitation_retry_recovers_missing_reply_route(tmp_path, monkeypatch):
    import zf.runtime.channel_router as router
    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch)
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    payload = {"channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]}
    original = router.route_channel_message
    monkeypatch.setattr(router, "route_channel_message", lambda **kwargs: None)
    assert execute(service, writer, "channel-invite-member", payload)["ok"]
    assert not any(r["target_member_id"] == "qa" for r in project_channel(state, "ch-admit")["reply_requests"])
    monkeypatch.setattr(router, "route_channel_message", original)
    assert execute(service, writer, "channel-invite-member", payload)["status"] == "already_decided"
    assert execute(service, writer, "channel-invite-member", payload)["status"] == "already_decided"
    channel = project_channel(state, "ch-admit")
    assert sum(r["target_member_id"] == "qa" for r in channel["reply_requests"]) == 1
    events = writer.event_log.read_all()
    for kind in ["channel.member.invited", "channel.member.invitation.decided", "channel.discussion.continued"]:
        assert sum(e.type == kind and e.payload.get("member_id", "qa") == "qa" for e in events) == 1


def test_free_discussion_invitation_joins_quietly_and_can_be_mentioned(tmp_path, monkeypatch):
    state, writer, config, service, channel, request = fixture(tmp_path, monkeypatch, mode="libre_discuss")
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    monkeypatch.setattr("zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background", lambda **kwargs: None)
    synthesis = execute(service, writer, "channel-synthesis-request", {"channel_id": "ch-admit"})
    assert synthesis["ok"], synthesis
    channel = project_channel(state, "ch-admit")
    messages_before = len(channel["messages"])
    result = execute(service, writer, "channel-invite-member", {
        "channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"],
    })
    assert result["ok"], result
    channel = project_channel(state, "ch-admit")
    assert any(m["member_id"] == "qa" for m in channel["members"])
    assert len(channel["messages"]) == messages_before
    assert not channel["discussions"]
    assert not any(r["target_member_id"] == "qa" for r in channel["reply_requests"])
    body = channel_message_event_payload(state, {"channel_id": "ch-admit", "thread_id": "main", "message_id": "ask-qa",
        "member_id": "operator", "role": "user", "text": "@qa 请从测试角度看看", "mentions": ["qa"]}, created_by="web")
    message = writer.emit("channel.message.posted", actor="web", payload=body)
    result = route_channel_message(state_dir=state, writer=writer, message_event=message, message_payload=body,
                                  actor="web", source="test", project_root=tmp_path, config=config, dispatch_inline=False)
    assert result.targets == ["qa"]
    pack = next(p for p in project_channel(state, "ch-admit")["context_packs"] if p["trigger_message_id"] == "ask-qa")
    assert pack["semantic_source_complete"]
