"""Required source completeness survives historical-output and duplicate-body projection."""
from zf.runtime.channel_semantic_sources import build_semantic_source_bundle
from zf.runtime.channel_source_projection import intern_source_contents


def channel(*, pipeline=True, owner_draft=False):
    return {"channel_id": "ch", "discussions": {"main": {"requirement_message_id": "u"}},
            "reply_requests": [{"request_id": "reply-old", "message_id": "old-trigger"}],
            "messages": [
                {"message_id": "u", "role": "user", "text": "不要透露其他座位身份"},
                {"message_id": "old-trigger", "role": "user", "source": "runtime", "refs": {"synthesis_request_id": "old"}},
                {"message_id": "old-draft", "role": "user" if owner_draft else "assistant", "text": "old contract " * 1000,
                 "refs": {"request_id": "reply-old"}},
                {"message_id": "correction", "role": "user", "text": "恢复后不能重新抽牌"},
                {"message_id": "dissent", "role": "assistant", "text": "保留反对意见：恢复需要对局版本绑定"},
                {"message_id": "new", "role": "user", "source": "runtime", "refs": {
                    "synthesis_request_id": "new", **({"synthesis_pipeline": "checkpointed.v1"} if pipeline else {})}}]}


def test_historical_generated_bodies_do_not_consume_required_budget_but_provenance_remains():
    result = build_semantic_source_bundle(channel(), thread_id="main", trigger_message_id="new", state_dir=None, max_source_chars=100)
    assert result["complete"], result["reason"]
    assert {r["message_id"] for r in result["documents"]} == {"u", "correction", "dissent"}
    archived = result["archived_sources"]
    assert archived[0]["message_id"] == "old-draft" and archived[0]["message_body_digest"]
    assert archived[0]["message_body_ref"].startswith("event:")
    legacy = build_semantic_source_bundle(channel(pipeline=False), thread_id="main", trigger_message_id="new", state_dir=None, max_source_chars=100)
    assert not legacy["complete"] and "budget_exceeded" in legacy["reason"]


def test_owner_supplied_draft_is_never_mistaken_for_generated_history():
    result = build_semantic_source_bundle(channel(owner_draft=True), thread_id="main", trigger_message_id="new", state_dir=None, max_source_chars=20000)
    assert result["complete"]
    assert any(r["message_id"] == "old-draft" and "old contract" in r["content"] for r in result["documents"])
    assert result["archived_sources"] == []


def test_owner_explicit_reply_keeps_generated_draft_as_current_candidate_source():
    data = channel()
    data["messages"][3]["reply_to_message_id"] = "old-draft"
    result = build_semantic_source_bundle(data, thread_id="main", trigger_message_id="new", state_dir=None, max_source_chars=20000)
    assert result["complete"]
    assert any(r["message_id"] == "old-draft" for r in result["documents"])
    assert not result["archived_sources"]


def test_interning_preserves_every_identity_and_every_byte_without_mutating_canonical_pack():
    body = "规则与验收\n" * 300
    pack = {"semantic_source_documents": [{"kind": "channel_attachment", "artifact_ref": "a", "content": body},
                                        {"kind": "channel_prd_source", "artifact_ref": "b", "content": body}]}
    view = intern_source_contents(pack)
    assert len(view["semantic_source_contents"]) == 1
    assert [r["artifact_ref"] for r in view["semantic_source_documents"]] == ["a", "b"]
    for item in view["semantic_source_documents"]:
        assert view["semantic_source_contents"][item["content_ref"]] == body
    assert all(row["content"] == body for row in pack["semantic_source_documents"])
