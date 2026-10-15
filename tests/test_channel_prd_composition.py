"""Source-bound composition preserves a full document through the real caller."""
import copy
import hashlib
import json

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_prd_composition import expand_prd_composition, expand_prd_source_attestation
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_semantic_sources import _stable_digest
from zf.runtime.channel_sidecar import channel_context_pack_event_payload, hydrate_channel_context_pack_payload


def fixture():
    content = "Operator wrapper\n# Product\n\n" + "| field | required |\n" * 3000 + "\nOld rule.\nEND PRODUCT\nOperator footer"
    digest = hashlib.sha256(content.encode()).hexdigest()
    source = {"message_id": "source", "message_body_digest": digest, "kind": "owner_input", "content": content}
    manifest = [{key: value for key, value in source.items() if key != "content"}]
    pack = {"context_pack_id": "ctx", "channel_id": "ch", "thread_id": "origin", "semantic_source_complete": True,
            "semantic_source_required": True, "semantic_source_documents": [source], "semantic_source_manifest": manifest,
            "semantic_source_manifest_digest": _stable_digest(manifest), "semantic_source_required_digests": [digest]}
    body = {"summary": "Overview", "prd_composition": {"source_message_id": "source", "source_message_digest": digest,
            "start_anchor": "# Product", "end_anchor": "END PRODUCT", "edits": [{"old": "Old rule.", "new": "New decided rule."}],
            "appendix_markdown": "## Decisions\nKeep full field table.\nAC-1: Every field remains."},
            "requirement_coverage": [{"id": "REQ-1", "source_refs": ["source"], "section": "Product", "acceptance_ids": ["AC-1"], "status": "covered"}],
            "acceptance_criteria": [{"id": "AC-1", "criterion": "Every field remains."}], "decision_log": [],
            "consumed_message_digests": [digest], "open_questions": []}
    return {"channel_id": "ch", "context_packs": {"ctx": pack}}, {"thread_id": "origin", "context_pack_id": "ctx"}, body


@pytest.mark.parametrize("attest", [False, True])
def test_composed_full_source_reaches_canonical_prd_download_and_review_identity(tmp_path, attest):
    channel, request, body = fixture()
    if attest:
        pack = channel_context_pack_event_payload(tmp_path, channel["context_packs"]["ctx"], created_by="test")
        # The production projector hydrates context bodies before this caller.
        channel["context_packs"] = {"ctx": hydrate_channel_context_pack_payload(tmp_path, pack, strict=True)}
        body.pop("consumed_message_digests")
        body["source_attestation"] = {"context_pack_id": "ctx", "context_pack_sha256": pack["context_pack_sha256"], "all_required_sources_read": True}
    original = copy.deepcopy(body)
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    emit_structured_reply_events(state_dir=tmp_path, writer=writer, channel=channel, request=request,
        message={"refs": {"synthesis_request_id": "synth", "prd_document_contract": "full.v1"}},
        reply=json.dumps({"channel_synthesis": body}), reply_event_id="reply", actor="test", source="test")
    events = writer.event_log.read_all()
    published = next(event.payload for event in events if event.type == "channel.synthesis.proposed")
    document = json.loads((tmp_path / published["artifact_ref"]).read_text())
    markdown = document["body"]["markdown"]
    assert markdown == (tmp_path / published["spec_path"]).read_text()
    assert markdown.count("| field | required |") == 3000
    assert "New decided rule." in markdown and "Old rule." not in markdown
    assert "Operator wrapper" not in markdown and "Operator footer" not in markdown
    assert "END PRODUCT" in markdown and "AC-1" in markdown
    consensus = next(event.payload for event in events if event.type == "channel.consensus.proposed")
    assert consensus["artifact_digest"] == published["artifact_digest"]
    assert body == original


@pytest.mark.parametrize("fault", ["digest", "foreign_source", "foreign_thread", "partial_context", "ambiguous_anchor", "reversed_anchors", "missing_edit", "overlap", "both_formats", "damaged_sidecar"])
def test_composition_rejects_unbound_or_ambiguous_edits(tmp_path, fault):
    channel, request, body = fixture()
    recipe = body["prd_composition"]
    pack = channel["context_packs"]["ctx"]
    if fault == "digest": recipe["source_message_digest"] = "wrong"
    if fault == "foreign_source": recipe["source_message_id"] = "another-message"
    if fault == "foreign_thread": pack["thread_id"] = "another-thread"
    if fault == "partial_context": pack["semantic_source_complete"] = False
    if fault == "ambiguous_anchor": recipe["start_anchor"] = "| field | required |"
    if fault == "reversed_anchors": recipe["start_anchor"], recipe["end_anchor"] = recipe["end_anchor"], recipe["start_anchor"]
    if fault == "missing_edit": recipe["edits"][0]["old"] = "not in source"
    if fault == "overlap": recipe["edits"].append({"old": "rule.", "new": "different"})
    if fault == "both_formats": body["prd_markdown"] = "An unrelated short document"
    if fault == "damaged_sidecar":
        persisted = channel_context_pack_event_payload(tmp_path, pack, created_by="test")
        channel["context_packs"] = {"ctx": persisted}
        # Tamper the immutable sidecar after its descriptor was bound.
        path = next((tmp_path / "channels").rglob("ctx-*.json"))
        path.write_text("{}")
    _, error = expand_prd_composition(body, channel=channel, request=request, state_dir=tmp_path)
    assert error


@pytest.mark.parametrize("fault", ["digest", "identity", "undeclared", "mixed", "unpersisted"])
def test_source_attestation_cannot_infer_or_claim_another_pack(tmp_path, fault):
    channel, request, body = fixture()
    original_pack = channel["context_packs"]["ctx"]
    pack = channel_context_pack_event_payload(tmp_path, original_pack, created_by="test")
    channel["context_packs"] = {"ctx": pack}
    body.pop("consumed_message_digests")
    body["source_attestation"] = declaration = {"context_pack_id": "ctx", "context_pack_sha256": pack["context_pack_sha256"], "all_required_sources_read": True}
    if fault == "digest": declaration["context_pack_sha256"] = "wrong"
    if fault == "identity": declaration["context_pack_id"] = "another-context"
    if fault == "undeclared": declaration["all_required_sources_read"] = False
    if fault == "mixed": body["consumed_message_digests"] = []
    if fault == "unpersisted": channel["context_packs"] = {"ctx": original_pack}
    _, error = expand_prd_source_attestation(body, channel=channel, request=request, state_dir=tmp_path)
    assert error


def test_attestation_expands_only_artifacts_in_the_attested_snapshot(tmp_path):
    channel, request, body = fixture()
    original = channel["context_packs"]["ctx"]
    original["contribution_index"] = [{"contract_status": "structured", "artifact_ref": "member.json", "artifact_digest": "m"}, {"contract_status": "unstructured", "artifact_ref": "ignore.json"}]
    original["cross_review_index"] = [{"status": "completed", "artifact_ref": "review.json", "artifact_digest": "r"}, {"status": "pending", "artifact_ref": "later.json"}]
    pack = channel_context_pack_event_payload(tmp_path, original, created_by="test")
    channel["context_packs"] = {"ctx": pack}
    body.pop("consumed_message_digests")
    body["source_attestation"] = {"context_pack_id": "ctx", "context_pack_sha256": pack["context_pack_sha256"], "all_required_sources_read": True}
    expanded, error = expand_prd_source_attestation(body, channel=channel, request=request, state_dir=tmp_path)
    assert not error
    assert expanded["consumed_contribution_refs"] == ["member.json", "review.json"]
    assert expanded["consumed_contribution_digests"] == ["m", "r"]
    assert expanded["consumed_message_digests"] == original["semantic_source_required_digests"]
    assert "consumed_message_digests" not in body


@pytest.mark.parametrize("fault", ["", "digest", "anchors", "mixed", "foreign_kind"])
def test_selected_file_composition_preserves_entire_source_and_requires_bound_identity(tmp_path, fault):
    channel, request, body = fixture()
    pack = channel["context_packs"]["ctx"]
    source = pack["semantic_source_documents"][0]
    digest = source.pop("message_body_digest")
    source.pop("message_id")
    source.update(kind="channel_prd_source", artifact_ref="sources/immutable.md", artifact_digest=digest)
    pack["semantic_source_manifest"] = [{k: v for k, v in source.items() if k != "content"}]
    body["prd_composition"] = {"source_ref": source["artifact_ref"], "source_digest": digest,
        "edits": [{"old": "Old rule.", "new": "Owner deferred database choice."}], "appendix_markdown": "## Deferred\nq-db"}
    if fault == "digest": body["prd_composition"]["source_digest"] = "wrong"
    if fault == "anchors": body["prd_composition"]["start_anchor"] = "# Product"
    if fault == "mixed": body["prd_composition"]["source_message_id"] = "source"
    if fault == "foreign_kind": source["kind"] = "contribution"
    result, error = expand_prd_composition(body, channel=channel, request=request, state_dir=tmp_path)
    if fault:
        assert error
    else:
        assert not error
        assert result["prd_markdown"] == source["content"].replace("Old rule.", "Owner deferred database choice.") + "\n\n## Deferred\nq-db"
        assert result["prd_markdown"].count("| field | required |") == 3000
