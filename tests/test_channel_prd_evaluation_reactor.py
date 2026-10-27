"""The actual Channel reactor entrypoint dispatches version-bound evaluation."""

from __future__ import annotations

import threading
import uuid
import json
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_attachments import capture_channel_attachment
from zf.runtime.channel_contract_artifacts import persist_channel_prd, persist_channel_prd_readiness
from zf.runtime.channel_prd_evaluation_reactor import _version_evidence, react_channel_prd_evaluation_requested
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.channel_message_ingress import _validate_ingress_message
from zf.runtime.channel_prd_document import render_prd_document
from zf.runtime.orchestrator_reactor import EventReactorMixin


@pytest.mark.parametrize("attachment_fault", ["", "missing", "tampered", "scope", "extra_missing", "extra_tampered", "extra_scope", "extra_identity"])
def test_reactor_dispatches_independent_source_and_target_once(tmp_path, monkeypatch, attachment_fault):
    state = tmp_path / "state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    channel_id = "ch-eval"
    thread_id = "kanban:project"
    writer.emit("channel.created", actor="web", payload={
        "channel_id": channel_id, "owner_actor_ref": "web"})
    source_payload = channel_message_event_payload(state, {
        "channel_id": channel_id, "thread_id": thread_id, "message_id": "source-1",
        "member_id": "operator", "role": "user", "text": "原始要求：必须保留权限。"},
        created_by="test")
    writer.emit("channel.message.posted", actor="web", correlation_id=channel_id,
                payload=source_payload)
    writer.emit("channel.discussion.started", actor="web", correlation_id=channel_id, payload={
        "channel_id": channel_id, "thread_id": thread_id,
        "requirement_message_id": "source-1", "mode": "multi_lens"})
    attachment_text = "# 原始业务附件\n完整保留车辆转移与库存回滚规则。\nEND-OF-REQUIREMENT"
    attachment = capture_channel_attachment(
        state, channel_id=channel_id,
        thread_id="other" if attachment_fault == "scope" else thread_id,
        name="original.md", mime="text/markdown", data=attachment_text.encode())
    attachment_path = state / attachment["body_ref"]["ref"]
    if attachment_fault == "missing":
        attachment_path.unlink()
    elif attachment_fault == "tampered":
        attachment_path.write_text("tampered")
    full_source = channel_message_event_payload(state, {
        "channel_id": channel_id, "thread_id": thread_id, "message_id": "source-2",
        "member_id": "operator", "role": "user", "text": "完整来源：权限之外还有失败回滚。",
        "refs": {"evaluation_source_document": True, "attachments": [attachment]}}, created_by="test")
    writer.emit("channel.message.posted", actor="web", correlation_id=channel_id,
                payload=full_source)
    extra_attachment = capture_channel_attachment(
        state, channel_id=channel_id, thread_id="other" if attachment_fault == "extra_scope" else thread_id, name="supplement.md",
        mime="text/markdown", data="审计日志不可省略。".encode())
    if attachment_fault == "extra_missing":
        (state / extra_attachment["body_ref"]["ref"]).unlink()
    elif attachment_fault == "extra_tampered":
        (state / extra_attachment["body_ref"]["ref"]).write_text("tampered")
    supplementary = channel_message_event_payload(state, {
        "channel_id": channel_id, "thread_id": thread_id, "message_id": "unmarked-extra",
        "member_id": "operator", "role": "user", "text": "另外必须提供审计日志。",
        "refs": {"attachments": [extra_attachment]}}, created_by="test")
    writer.emit("channel.message.posted", actor="web", correlation_id=channel_id,
                payload=supplementary)
    target_attachment = capture_channel_attachment(
        state, channel_id=channel_id, thread_id=thread_id, name="candidate.md",
        mime="text/markdown", data=b"AUTHOR-TARGET-MUST-NOT-ENTER-SOURCE")
    ordinary = channel_message_event_payload(state, {
        "channel_id": channel_id, "thread_id": thread_id, "message_id": "owner-comment",
        "member_id": "operator", "role": "user", "text": "稍后考虑其他问题。",
        "refs": {"attachments": [target_attachment]}},
        created_by="test")
    writer.emit("channel.message.posted", actor="web", correlation_id=channel_id,
                payload=ordinary)
    readiness = {"verdict": "ready", "implementation_start": True, "gaps": []}
    readiness_descriptor = persist_channel_prd_readiness(
        state, channel_id=channel_id, thread_id=thread_id, revision=1,
        body=readiness, created_by="leader", source_event_id="source-1")
    descriptor = persist_channel_prd(
        state, channel_id=channel_id, thread_id=thread_id, revision=1,
        previous_ref="", previous_digest="",
        body={"title": "PRD", "markdown": "# 最终 PRD\n必须保留权限。",
              "synthesis": {"readiness": readiness, "dissent": ["保留异议"],
                            "source_coverage": [{"kind": kind, "message_id": message["message_id"],
                                                 "message_body_ref": message["refs"]["message_body"]["ref"],
                                                 "message_body_digest": "wrong-digest" if attachment_fault == "extra_identity" and message["message_id"] == "unmarked-extra" else message["refs"]["message_body"]["sha256"]}
                                                for kind, message in [("requirement", source_payload),
                                                    ("owner_input", full_source), ("owner_input", supplementary),
                                                    ("owner_input", ordinary)]] + [
                                                {"kind": "channel_attachment", "message_id": message_id,
                                                 "message_body_ref": ref["uri"], "message_body_digest": ref["hash"]}
                                                for message_id, ref in [("source-2", attachment),
                                                    ("unmarked-extra", extra_attachment), ("owner-comment", target_attachment)]],
                            "acceptance_criteria": [{"id": "AC-01", "description": "权限"}],
                            "verification_commands": [{"id": "VC-01", "command": "pytest tests/test_permissions.py"}]}},
        readiness_descriptor=readiness_descriptor, created_by="leader", source_event_id="source-1")
    writer.emit("channel.synthesis.proposed", actor="leader", correlation_id=channel_id,
                payload={"channel_id": channel_id, "thread_id": thread_id,
                         "prd_revision": 1, "artifact_ref": descriptor["ref"],
                         "artifact_digest": descriptor["sha256"]})
    late = channel_message_event_payload(state, {
        "channel_id": channel_id, "thread_id": thread_id, "message_id": "late-correction",
        "member_id": "operator", "role": "user", "text": "LATE-CORRECTION-ONLY-FOR-NEXT-REVISION"}, created_by="test")
    writer.emit("channel.message.posted", actor="web", correlation_id=channel_id, payload=late)
    writer.emit("channel.discussion.started", actor="web", correlation_id=channel_id, payload={
        "channel_id": channel_id, "thread_id": thread_id, "requirement_message_id": "late-correction", "mode": "multi_lens"})
    writer.emit("channel.question.resolved", actor="web", correlation_id=channel_id, payload={
        "channel_id": channel_id, "thread_id": thread_id, "question_id": "LATE-QUESTION-FOR-NEXT-REVISION", "answer": "later"})
    for member, ref, digest, thread in [
        ("same-version", descriptor["ref"], descriptor["sha256"], thread_id),
        ("old-version", "channels/ch-eval/prd/r0.json", "old", thread_id),
        ("other-thread", descriptor["ref"], descriptor["sha256"], "other"),
        ("wrong-digest", descriptor["ref"], "incorrect", thread_id),
    ]:
        writer.emit("channel.consensus.signed", actor=member, correlation_id=channel_id,
                    payload={"channel_id": channel_id, "thread_id": thread,
                             "artifact_ref": ref, "artifact_digest": digest})
    request = writer.emit("channel.prd_evaluation.requested", actor="web",
                          correlation_id=channel_id, payload={
                              "request_id": str(uuid.uuid4()), "channel_id": channel_id,
                              "thread_id": thread_id, "prd_revision": 1,
                              "artifact_ref": descriptor["ref"],
                              "artifact_digest": descriptor["sha256"], "role": "full"})
    for admitted_channel, admitted_thread, decision in [
        (channel_id, thread_id, "approved"),
        (channel_id, "other", "rejected"),
        ("other-channel", thread_id, "rejected"),
    ]:
        writer.emit("channel.member.invitation.decided", actor="web", correlation_id=admitted_channel,
                    payload={"channel_id": admitted_channel, "thread_id": admitted_thread,
                             "member_id": "qa", "proposal_id": "qa-invitation", "decision": decision})
    host = SimpleNamespace(state_dir=state, event_log=log, event_writer=writer)
    called = threading.Event()
    captured = []

    def fake_evaluate_output(**kwargs):
        captured.append(kwargs["item"])
        called.set()
        return {"verdict": "INDETERMINATE"}

    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.evaluate_output", fake_evaluate_output)
    if attachment_fault:
        class InlineThread:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.threading.Thread", InlineThread)
    EventReactorMixin._on_channel_prd_evaluation_requested(host, request)
    if attachment_fault:
        assert not captured
        failures = [row for row in log.read_all() if row.type == "output.evaluation.failed"]
        assert len(failures) == 1
        assert failures[0].payload["verdict"] == "INDETERMINATE"
        assert ("message" if attachment_fault == "extra_identity" else "attachment") in failures[0].payload["reason"].lower()
        return
    assert called.wait(3)
    EventReactorMixin._on_channel_prd_evaluation_requested(host, request)
    assert len(captured) == 1
    assert "原始要求：必须保留权限。" in captured[0].source_text
    assert "完整来源：权限之外还有失败回滚。" in captured[0].source_text
    assert attachment_text in captured[0].source_text
    assert attachment["hash"] in captured[0].source_text
    assert "AUTHOR-TARGET-MUST-NOT-ENTER-SOURCE" not in captured[0].source_text
    assert "稍后考虑其他问题。" not in captured[0].source_text
    assert "稍后考虑其他问题。" in captured[0].decision_text
    assert "另外必须提供审计日志。" in captured[0].decision_text
    assert "审计日志不可省略。" in captured[0].decision_text
    assert extra_attachment["hash"] in captured[0].decision_text
    assert "LATE-CORRECTION-ONLY-FOR-NEXT-REVISION" not in captured[0].decision_text
    assert "LATE-CORRECTION-ONLY-FOR-NEXT-REVISION" not in captured[0].source_text
    assert "LATE-QUESTION-FOR-NEXT-REVISION" not in captured[0].decision_text
    assert "CANONICAL MARKDOWN:\n# 最终 PRD" in captured[0].target_text
    assert "CANONICAL STRUCTURED CONTRACT" in captured[0].target_text
    assert "pytest tests/test_permissions.py" in captured[0].target_text
    assert '"implementation_start": true' in captured[0].target_text
    assert "保留异议" in captured[0].target_text
    evidence = json.loads(captured[0].evidence_text)
    assert evidence["readiness"]["implementation_start"] is True
    assert evidence["readiness_descriptor"]["sha256"] == readiness_descriptor["sha256"]
    assert [row["actor"] for row in evidence["version_bound_events"]] == ["same-version"]
    assert [row["payload"]["decision"] for row in evidence["channel_membership_events"]] == ["approved"]
    assert evidence["channel_membership_events"][0]["actor"] == "web"
    decision_rows = json.loads(captured[0].decision_text)
    admission_rows = [row for row in decision_rows if row.get("kind") == "channel_membership_event"]
    assert admission_rows == [dict(kind="channel_membership_event", **row)
                              for row in evidence["channel_membership_events"]]
    assert admission_rows[0]["payload"]["decision"] == "approved"
    assert admission_rows[0]["event_id"]
    assert admission_rows[0]["actor"] == "web"
    binding = evidence["digest_binding"]
    assert binding["canonical_artifact"]["ref"] == descriptor["ref"]
    assert binding["canonical_artifact"]["sha256"] == descriptor["sha256"]
    assert binding["evaluation_target"]["sha256"] == captured[0].manifest()["target"]["sha256"]
    assert binding["canonical_artifact"]["sha256"] != binding["evaluation_target"]["sha256"]
    assert "raw JSON artifact" in binding["relationship"]
    assert captured[0].target_ref == descriptor["ref"]
    assert captured[0].scope == "full"


@pytest.mark.parametrize("defect", ["missing", "digest", "identity"])
def test_version_evidence_rejects_incomplete_or_corrupt_readiness(tmp_path, defect):
    descriptor = persist_channel_prd_readiness(
        tmp_path, channel_id="ch", thread_id="main", revision=1,
        body={"verdict": "ready"}, created_by="test", source_event_id="source")
    document = {"channel_id": "ch", "thread_id": "main", "revision": 1,
                "readiness_ref": descriptor["ref"], "readiness_digest": descriptor["sha256"]}
    if defect == "missing":
        document["readiness_ref"] = ""
    elif defect == "digest":
        document["readiness_digest"] = "0" * 64
    else:
        document["revision"] = 2
    with pytest.raises((ValueError, RuntimeError)):
        _version_evidence(SimpleNamespace(state_dir=tmp_path), document, [])


def test_reactor_reports_missing_evidence_without_invoking_provider(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    host = SimpleNamespace(state_dir=tmp_path, event_log=log, event_writer=writer)

    class InlineThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    def unavailable(*args):
        raise ValueError("canonical PRD readiness ref/digest is unavailable")

    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.threading.Thread", InlineThread)
    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor._request_item", unavailable)
    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.evaluate_output",
                        lambda **kwargs: pytest.fail("missing evidence must not reach provider"))
    event = writer.emit("channel.prd_evaluation.requested", payload={
        "request_id": "missing-evidence", "channel_id": "ch", "thread_id": "main",
        "artifact_ref": "channels/ch/prd/r1.json", "prd_revision": 1, "role": "full"})
    react_channel_prd_evaluation_requested(host, event)
    react_channel_prd_evaluation_requested(host, event)
    failed = [row for row in log.read_all() if row.type == "output.evaluation.failed"]
    assert len(failed) == 1
    assert failed[0].payload["verdict"] == "INDETERMINATE"
    assert "readiness" in failed[0].payload["reason"]


def test_original_source_marker_is_owner_only():
    options = {"channel": {"owner_actor_ref": "owner:operator"},
               "thread_id": "main", "member_id": "operator", "role": "user",
               "reply_to_message_id": "", "refs": {"evaluation_source_document": True}}
    assert _validate_ingress_message(**options, actor="web", surface="web") == ""
    assert "Owner" in _validate_ingress_message(**options, actor="agent", surface="cli")
    assert "Owner" in _validate_ingress_message(
        **{**options, "role": "assistant"}, actor="web", surface="web")


def test_prd_export_exposes_task_handoff_fields():
    body = render_prd_document({
        "prd_markdown": "# 活动报名\n\n## 验收\n报名不得超额。",
        "acceptance_criteria": [{"id": "AC-01", "description": "不得超额"}],
        "verification_commands": [{"id": "VC-01", "command": "pytest tests/test_booking.py"}],
        "out_of_scope": ["支付"],
    }, channel_id="ch-1", thread_id="main", source_refs=[])
    assert "## 实施交接合同" in body
    assert "pytest tests/test_booking.py" in body
    assert "### 验收目录" in body
    assert "AC-01" in body
    assert "支付" in body


@pytest.mark.parametrize("fault", ["", "missing", "tampered", "thread", "scope", "request"])
def test_file_source_evaluation_uses_bound_snapshot_without_discussion(tmp_path, monkeypatch, fault):
    from zf.runtime.sidecar_refs import write_sidecar_text

    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-file", "owner_actor_ref": "web"})
    source = write_sidecar_text(
        tmp_path, "channels/prd-sources/source.md", "ORIGINAL complete requirement; END",
        kind="channel_prd_source", schema_version="channel.prd-source.v1",
        content_type="text/markdown", created_by="web", source_event_id="selection",
        access_scope={"channel_id": "ch-file", "thread_id": "other" if fault == "scope" else "main"})
    writer.emit("channel.synthesis.requested", actor="web", correlation_id="ch-file", payload={
        "channel_id": "ch-file", "thread_id": "other" if fault == "thread" else "main",
        "request_id": "wrong" if fault == "request" else "synthesis", "prd_source_file": source})
    readiness = persist_channel_prd_readiness(tmp_path, channel_id="ch-file", thread_id="main",
        revision=1, body={"verdict": "ready"}, created_by="author", source_event_id="synthesis")
    target = persist_channel_prd(tmp_path, channel_id="ch-file", thread_id="main", revision=1,
        previous_ref="", previous_digest="", body={"markdown": "AUTHOR TARGET ONLY", "synthesis": {}},
        readiness_descriptor=readiness, created_by="author", source_event_id="synthesis")
    writer.emit("channel.synthesis.proposed", actor="author", correlation_id="ch-file", payload={
        "channel_id": "ch-file", "thread_id": "main", "request_id": "synthesis", "prd_revision": 1,
        "artifact_ref": target["ref"], "artifact_digest": target["sha256"]})
    if fault == "missing":
        (tmp_path / source["ref"]).unlink()
    elif fault == "tampered":
        (tmp_path / source["ref"]).write_text("altered")
    captured = []

    class InlineThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.threading.Thread", InlineThread)
    monkeypatch.setattr("zf.runtime.channel_prd_evaluation_reactor.evaluate_output",
                        lambda **kw: captured.append(kw["item"]) or {"verdict": "PASS"})
    request = writer.emit("channel.prd_evaluation.requested", correlation_id="ch-file", payload={
        "request_id": str(uuid.uuid4()), "channel_id": "ch-file", "thread_id": "main",
        "prd_revision": 1, "artifact_ref": target["ref"], "artifact_digest": target["sha256"]})
    host = SimpleNamespace(state_dir=tmp_path, event_log=log, event_writer=writer)
    EventReactorMixin._on_channel_prd_evaluation_requested(host, request)
    if fault:
        assert not captured
        assert any(e.type == "output.evaluation.failed" for e in log.read_all())
    else:
        assert len(captured) == 1
        assert "ORIGINAL complete requirement; END" in captured[0].source_text
        assert source["sha256"] in captured[0].source_text
        assert "AUTHOR TARGET ONLY" not in captured[0].source_text
        assert "AUTHOR TARGET ONLY" in captured[0].target_text


def test_legacy_prd_keeps_published_anchor_after_restart_without_owner_metadata(tmp_path):
    from zf.runtime.channel_prd_evaluation_reactor import _request_item

    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    writer.emit("channel.created", actor="web", payload={"channel_id": "legacy"})
    original = channel_message_event_payload(tmp_path, {"channel_id": "legacy", "thread_id": "main",
        "message_id": "original", "member_id": "operator", "role": "user", "text": "原始需求：支持撤销。"}, created_by="web")
    writer.emit("channel.message.posted", actor="web", correlation_id="legacy", payload=original)
    writer.emit("channel.discussion.started", actor="web", correlation_id="legacy", payload={
        "channel_id": "legacy", "thread_id": "main", "requirement_message_id": "original"})
    ready = persist_channel_prd_readiness(tmp_path, channel_id="legacy", thread_id="main", revision=1,
        body={"verdict": "ready"}, created_by="author", source_event_id="source")
    document = persist_channel_prd(tmp_path, channel_id="legacy", thread_id="main", revision=1,
        previous_ref="", previous_digest="", body={"markdown": "# PRD", "synthesis": {}},
        readiness_descriptor=ready, created_by="author", source_event_id="source")
    writer.emit("channel.synthesis.proposed", actor="author", correlation_id="legacy", payload={
        "channel_id": "legacy", "thread_id": "main", "prd_revision": 1,
        "artifact_ref": document["ref"], "artifact_digest": document["sha256"],
        "readiness_ref": ready["ref"], "readiness_digest": ready["sha256"]})
    later = channel_message_event_payload(tmp_path, {"channel_id": "legacy", "thread_id": "main",
        "message_id": "later", "member_id": "operator", "role": "user", "text": "下一版不支持撤销。"}, created_by="web")
    writer.emit("channel.message.posted", actor="web", correlation_id="legacy", payload=later)
    writer.emit("channel.discussion.started", actor="web", correlation_id="legacy", payload={
        "channel_id": "legacy", "thread_id": "main", "requirement_message_id": "later"})
    event = writer.emit("channel.prd_evaluation.requested", actor="web", correlation_id="legacy", payload={
        "channel_id": "legacy", "thread_id": "main", "prd_revision": 1,
        "artifact_ref": document["ref"], "artifact_digest": document["sha256"]})
    item = _request_item(SimpleNamespace(state_dir=tmp_path, event_log=writer.event_log), event)
    assert "原始需求：支持撤销。" in item.source_text
    assert "下一版不支持撤销。" not in item.source_text
