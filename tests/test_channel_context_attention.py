import pytest

from tests.test_channel_discussion_attention import _channel
from zf.runtime.channel_discussion_attention import project_discussion_attention


@pytest.mark.parametrize("mapping", [True, False])
@pytest.mark.parametrize("difference", ["", "digest", "artifact", "member", "thread", "incomplete", "older"])
def test_review_rebuild_retires_only_exact_target_version_rejection(difference, mapping):
    channel = _channel()
    refs = {"consensus_review_id": "old", "artifact_ref": "prd/r3.json", "artifact_digest": "digest"}
    retry_refs = {**refs, "consensus_review_id": "retry"}
    if difference == "digest":
        retry_refs["artifact_digest"] = "another"
    if difference == "artifact":
        retry_refs["artifact_ref"] = "prd/r2.json"
    channel["messages"] = [{"message_id": "old", "refs": refs}, {"message_id": "retry", "refs": retry_refs}]
    if mapping:
        channel["messages"] = {row["message_id"]: row for row in channel["messages"]}
    rejected = {"context_pack_id": "old", "thread_id": "main", "target_member_id": "architect",
                "trigger_message_id": "old", "status": "rejected", "ts": "2026-08-19T02:04:00Z"}
    built = {**rejected, "context_pack_id": "retry", "trigger_message_id": "retry", "status": "built",
             "semantic_source_complete": difference != "incomplete", "ts": "2026-08-19T02:05:00Z"}
    if difference == "member":
        built["target_member_id"] = "pm"
    if difference == "thread":
        built["thread_id"] = "other"
    if difference == "older":
        built["ts"] = "2026-08-19T02:03:00Z"
    channel["context_packs"] = {"old": rejected, "retry": built}
    attention = project_discussion_attention(channel, [], {})["main"]
    assert bool(attention["context_rejections"]) == bool(difference)
    assert channel["context_packs"]["old"] == rejected  # history remains intact
