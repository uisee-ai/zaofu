"""A signed review keeps structured evidence and its immutable version binding."""
import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_deliberation_contract import apply_consensus_review_reply
from zf.runtime.channel_projection import project_channel
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def setup_review(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("channel.created", actor="test", payload={"channel_id": "ch", "name": "Review"})
    writer.emit("channel.member.added", actor="test", payload={
        "channel_id": "ch", "member_id": "critic", "member_type": "provider_agent",
        "channel_role": "critic", "status": "active"})
    writer.emit("channel.consensus.proposed", actor="author", payload={
        "channel_id": "ch", "thread_id": "main", "artifact_ref": "prd/r1.json",
        "artifact_digest": "digest-1", "required_signers": ["critic"]})
    channel = project_channel(tmp_path, "ch")
    kwargs = dict(state_dir=tmp_path, writer=writer, channel=channel, channel_id="ch",
                  thread_id="main", member_id="critic", request={},
                  refs={"consensus_review_id": "review-1", "artifact_ref": "prd/r1.json",
                        "artifact_digest": "digest-1"}, reply_event_id="reply-1", actor="critic", source="test")
    return log, kwargs


def test_structured_evidence_survives_event_sidecar_and_projection(tmp_path):
    log, kwargs = setup_review(tmp_path)
    refs = [{"ref": f"prd/r1.json#section-{i}", "digest": "digest-1",
             "location": f"section {i}", "detail": f"Checked counterexample {i}"} for i in range(8)]
    review = {"verdict": "signed", "summary": "Scoped review: failure cases; UI not reviewed.",
              "artifact_digest": "digest-1", "evidence_refs": refs}
    apply_consensus_review_reply(**kwargs, review=review)
    event = next(e for e in log.read_all() if e.type == "channel.consensus.signed")
    assert event.payload["evidence_refs"] == [r["ref"] for r in refs]
    saved = hydrate_sidecar_ref(tmp_path, {"ref": event.payload["review_ref"],
                                        "sha256": event.payload["review_digest"]}).payload
    assert saved["review"] == review
    assert saved["binding"]["artifact_digest"] == "digest-1"
    projected = project_channel(tmp_path, "ch")["consensus"]["main"]
    assert projected["reviews"]["critic"]["review_ref"] == event.payload["review_ref"]
    assert projected["reviews"]["critic"]["evidence_status"] == "recorded"
    # Replay uses canonical consensus state, not a second sign-off.
    kwargs["channel"] = project_channel(tmp_path, "ch")
    apply_consensus_review_reply(**kwargs, review=review)
    assert len([e for e in log.read_all() if e.type == "channel.consensus.signed"]) == 1


@pytest.mark.parametrize("refs", [[{}], [""], [None], [42], "prd/r1", [{"ref": 42}]])
def test_invalid_evidence_is_rejected_instead_of_silently_lost(tmp_path, refs):
    log, kwargs = setup_review(tmp_path)
    apply_consensus_review_reply(**kwargs, review={"verdict": "signed", "artifact_digest": "digest-1",
                                                 "summary": "Reviewed", "evidence_refs": refs})
    assert not any(e.type == "channel.consensus.signed" for e in log.read_all())
    assert any(e.type == "channel.consensus.review.rejected" for e in log.read_all())


def test_stale_review_is_not_preserved_as_current_approval(tmp_path):
    log, kwargs = setup_review(tmp_path)
    apply_consensus_review_reply(**kwargs, review={"verdict": "signed", "artifact_digest": "old",
                                                 "evidence_refs": ["prd/old"]})
    assert log.read_all()[-1].type == "channel.consensus.review.rejected"
    assert not (tmp_path / "artifacts/channel-reviews").exists()


def test_legacy_event_does_not_claim_recorded_reading_evidence(tmp_path):
    log, kwargs = setup_review(tmp_path)
    kwargs["writer"].emit("channel.consensus.signed", actor="critic", payload={
        "channel_id": "ch", "thread_id": "main", "member_id": "critic", "artifact_digest": "digest-1"})
    saved = project_channel(tmp_path, "ch")["consensus"]["main"]["reviews"]["critic"]
    assert saved["evidence_status"] == "legacy_unrecorded"
    assert saved["review_ref"] == ""
