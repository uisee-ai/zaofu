"""On-demand Channel reviews use the existing approved invitation and signer paths."""
from tests.test_channel_member_admission import fixture, propose, execute
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_prd_revision import consensus_mode_and_required_signers
from zf.runtime.channel_templates import materialize_channel_template


def test_default_channel_still_has_critic_and_opt_in_has_no_phantom_signer(tmp_path, monkeypatch):
    original, error = materialize_channel_template("prd-clarification")
    assert not error
    assert "critic" in [m["member_id"] for m in original["members"]]
    state, writer, config, service, channel, request = fixture(
        tmp_path, monkeypatch, template_id="prd-clarification-on-demand")
    session = channel["discussions"]["main"]
    assert "critic" not in session["roster"]
    assert "critic" not in session["review_roster"]
    assert not any(e.type == "channel.agent.reply.requested" and e.payload.get("target_member_id") == "critic"
                   for e in writer.event_log.read_all())
    _, signers = consensus_mode_and_required_signers(channel, thread_id="main")
    assert set(signers) == set(session["review_roster"])
    channel = propose(state, writer, channel, request)
    proposal = channel["member_invitation_proposals"][0]
    assert "qa" not in consensus_mode_and_required_signers(channel, thread_id="main")[1]
    assert channel["discussion_attention"]["main"]["reason"] == "member_invitation_pending"
    payload = {"channel_id": "ch-admit", "thread_id": "main", "member_id": "qa", "proposal_id": proposal["proposal_id"]}
    assert execute(service, writer, "channel-invite-member", payload)["ok"]
    again = execute(service, writer, "channel-invite-member", payload)
    assert again["status"] == "already_decided"
    channel = project_channel(state, "ch-admit")
    _, signers = consensus_mode_and_required_signers(channel, thread_id="main")
    assert "qa" in signers and "critic" not in signers
    assert signers.count("qa") == 1
    # Reprojection/restart retains the obligation instead of deriving it from current speakers.
    assert consensus_mode_and_required_signers(project_channel(state, "ch-admit"), thread_id="main")[1] == signers
