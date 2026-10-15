from tests.test_channel_collaboration_closure import CH, seed
from tests.test_channel_templates import _execute
from zf.runtime.channel_projection import project_channel


def test_owner_cannot_confirm_revision_blocker_but_can_confirm_revised_prd(tmp_path):
    state, log, writer, service = seed(tmp_path)

    def propose(revision):
        binding = {"channel_id": CH, "thread_id": "main", "artifact_ref": f"prd/r{revision}.json",
                   "artifact_digest": str(revision) * 64, "prd_revision": revision}
        writer.emit("channel.consensus.proposed", actor="tech_leader", payload={
            **binding, "required_signers": ["tech_leader", "qa_analyst"],
            "product_mode": "multi_lens", "readiness_verdict": "ready", "owner_actor_ref": "web"})
        return binding

    old = propose(1)
    # Even a previously accepted Owner confirmation cannot be replayed over a
    # later independent material blocker on the same version.
    assert _execute(service, writer, "channel-consensus-confirm", old)["ok"]
    writer.emit("channel.consensus.blocked", actor="qa_analyst", payload={
        **old, "member_id": "qa_analyst", "blocker_kind": "revision_request",
        "blocker_question": "Current run evidence can reuse stale success."})
    signed_before = len([e for e in log.read_all() if e.type == "channel.consensus.signed"])
    rejected = _execute(service, writer, "channel-consensus-confirm", old)
    assert not rejected["ok"] and rejected["_status_code"] == 409
    assert rejected["status"] == "review_revision_required"
    assert len([e for e in log.read_all() if e.type == "channel.consensus.signed"]) == signed_before
    assert not project_channel(state, CH)["consensus"]["main"]["reached_event_id"]

    new = propose(2)
    assert not project_channel(state, CH)["consensus"]["main"]["human_confirmed"]
    assert _execute(service, writer, "channel-consensus-confirm", old)["status"] == "consensus_stale"
    assert _execute(service, writer, "channel-consensus-confirm", new)["ok"]
    assert _execute(service, writer, "channel-consensus-confirm", new)["duplicate"]
    # Owner approval still cannot manufacture independent reviewer signatures.
    assert not project_channel(state, CH)["consensus"]["main"]["reached_event_id"]
