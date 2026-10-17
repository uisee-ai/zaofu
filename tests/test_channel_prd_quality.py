"""Regression tests for the PRD cross-layer quality contract."""

from zf.runtime.channel_prd_quality import (
    contribution_ledgers,
    validate_prd_quality_contract,
)


def _quality_body() -> dict:
    return {
        "quality_contract": "channel-prd-quality.v1",
        "classification": {
            "source_read_status": "complete",
            "artifact_readability_status": "verified",
        },
        "source_attestation": {"all_required_sources_read": True},
        "requirement_coverage": [{
            "id": "REQ-1",
            "source_refs": ["docx#1"],
            "evidence_refs": ["review:REQ-1"],
            "status": "covered",
        }, {
            "id": "RES-1",
            "kind": "research",
            "source_refs": ["research:1"],
            "evidence_refs": ["review:RES-1"],
            "adopted_conclusion": "采用事件溯源作为审计主线",
            "applicability_limit": "仅适用于本平台的闭环数据链路",
            "status": "covered",
        }],
        "consumed_contribution_refs": ["review-1", "contribution-1"],
        "canonical_cross_review_refs": ["review-1"],
        "supporting_contribution_refs": ["contribution-1"],
        "permission_audit_contract": {
            "deny_by_default": True,
            "event_fields": ["actor", "action", "resource", "decision"],
            "query_fields": ["channel_id", "thread_id", "actor"],
            "retention_policy": "audit records retained for 90 days",
            "owner": "security-owner",
        },
    }


def test_quality_contract_rejects_attestation_state_contradiction():
    body = _quality_body()
    body["classification"]["source_read_status"] = "partial"
    assert "source_read_status" in validate_prd_quality_contract(body)


def test_quality_contract_requires_direct_research_evidence_and_adoption():
    body = _quality_body()
    body["requirement_coverage"][1].pop("adopted_conclusion")
    assert "adopted_conclusion" in validate_prd_quality_contract(body)

    body = _quality_body()
    body["requirement_coverage"][0].pop("evidence_refs")
    assert "evidence_refs" in validate_prd_quality_contract(body)


def test_quality_contract_requires_disjoint_permission_and_contribution_ledgers():
    body = _quality_body()
    body["supporting_contribution_refs"] = ["review-1"]
    assert "disjoint" in validate_prd_quality_contract(body)

    body = _quality_body()
    body.pop("permission_audit_contract")
    assert "permission_audit_contract" in validate_prd_quality_contract(body)


def test_contribution_ledgers_are_derived_from_bound_context_pack():
    pack = {
        "cross_review_index": [{"status": "completed", "artifact_ref": "review-1"}],
        "contribution_index": [{"contract_status": "structured", "artifact_ref": "contribution-1"}],
    }
    assert contribution_ledgers(pack, ["contribution-1", "review-1"]) == {
        "canonical_cross_review_refs": ["review-1"],
        "supporting_contribution_refs": ["contribution-1"],
    }


def test_evaluator_preflight_uses_the_same_quality_contract(tmp_path):
    import pytest

    from types import SimpleNamespace

    from zf.core.events import EventLog, EventWriter
    from zf.runtime.channel_contract_artifacts import (
        persist_channel_prd,
        persist_channel_prd_readiness,
    )
    from zf.runtime.channel_prd_evaluation_reactor import _request_item
    from zf.runtime.channel_sidecar import channel_message_event_payload

    state = tmp_path / "state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    channel_id = "ch-quality"
    thread_id = "main"
    writer.emit("channel.created", actor="owner", correlation_id=channel_id,
                payload={"channel_id": channel_id, "owner_actor_ref": "owner"})
    message = channel_message_event_payload(state, {
        "channel_id": channel_id, "thread_id": thread_id, "message_id": "req",
        "member_id": "owner", "role": "user", "text": "原始需求",
    }, created_by="test")
    writer.emit("channel.message.posted", actor="owner", correlation_id=channel_id,
                payload=message)
    writer.emit("channel.discussion.started", actor="owner", correlation_id=channel_id,
                payload={"channel_id": channel_id, "thread_id": thread_id,
                         "requirement_message_id": "req"})
    readiness = persist_channel_prd_readiness(
        state, channel_id=channel_id, thread_id=thread_id, revision=1,
        body={"verdict": "needs_owner"}, created_by="owner", source_event_id="req",
    )
    invalid = _quality_body()
    invalid["classification"]["source_read_status"] = "partial"
    prd = persist_channel_prd(
        state, channel_id=channel_id, thread_id=thread_id, revision=1,
        previous_ref="", previous_digest="",
        body={"summary": "draft", "markdown": "# PRD", "synthesis": invalid},
        readiness_descriptor=readiness, created_by="owner", source_event_id="req",
    )
    writer.emit("channel.synthesis.proposed", actor="owner", correlation_id=channel_id,
                payload={"channel_id": channel_id, "thread_id": thread_id,
                         "prd_revision": 1, "artifact_ref": prd["ref"],
                         "artifact_digest": prd["sha256"]})
    request = writer.emit("channel.prd_evaluation.requested", actor="owner",
                          correlation_id=channel_id, payload={
                              "channel_id": channel_id, "thread_id": thread_id,
                              "prd_revision": 1, "artifact_ref": prd["ref"],
                              "artifact_digest": prd["sha256"]})
    host = SimpleNamespace(state_dir=state, event_log=log, event_writer=writer)
    with pytest.raises(ValueError, match="quality contract"):
        _request_item(host, request)
