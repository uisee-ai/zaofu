"""File delivery uses the same full PRD, revision, review and Owner gates."""
import hashlib
import json
from pathlib import Path

import pytest

from zf.core.events import EventLog, EventWriter
from zf.core.config.schema import ChannelConfig, ZfConfig
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_reply_prompt import channel_reply_response_contract
from zf.runtime.channel_prd_file import expand_prd_file, snapshot_prd_file
from zf.runtime.channel_document_source import ChannelDocumentError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background", lambda **kw: None)
    state = tmp_path / "custom-state"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch-file", "owner_actor_ref": "web"})
    for member, role in [("synth", "synthesizer"), ("dba", "dba"), ("critic", "critic")]:
        writer.emit("channel.member.invited", actor="web", payload={"channel_id": "ch-file", "member_id": member,
                    "channel_role": role, "member_type": "persona_agent", "backend": "fake", "permission_profile": "read_only"})
    writer.emit("channel.discussion.started", actor="web", payload={"channel_id": "ch-file", "thread_id": "main",
                "product_mode": "multi_lens", "roster": ["synth", "dba", "critic"], "synthesizer": "synth", "owner_actor_ref": "web"})
    writer.emit("channel.discussion.phase.changed", actor="web", payload={"channel_id": "ch-file", "thread_id": "main", "phase": "phase1_blind"})
    # A cancelled blind turn remains evidence; the explicit draft still needs DBA review.
    writer.emit("channel.agent.reply.requested", actor="test", payload={"channel_id": "ch-file", "thread_id": "main",
                "request_id": "old-dba", "target_member_id": "dba", "message_id": "old-blind"})
    writer.emit("channel.agent.reply.failed", actor="test", payload={"channel_id": "ch-file", "thread_id": "main",
                "request_id": "old-dba", "status": "cancelled", "reason": "cancelled by operator"})
    file = tmp_path / "PRD.r2.md"
    body = "# Complete PRD\n\n## Behavior\n" + "Full source row.\n" * 10000 + "\nEND OF FILE\n"
    file.write_text(body)
    writer.emit("channel.message.posted", actor="synth", payload={"channel_id": "ch-file", "thread_id": "main",
                "message_id": "delivery", "member_id": "synth", "text": f"Saved [PRD]({file})"})
    service = ControlledActionService(state, writer, project_root=tmp_path, actor="web", source="web", surface="web",
                                     config=ZfConfig(channel=ChannelConfig(semantic_source_max_chars=1_000_000)))
    return state, writer, service, file, body


def request(service, writer, file, body):
    event = writer.emit("runtime.action.requested", actor="web", payload={"action": "channel-synthesis-request"})
    return service.execute(action="channel-synthesis-request", requested_action="channel.synthesis.request", requested=event,
                           payload={"channel_id": "ch-file", "thread_id": "main", "source_file": {
                               "ref": str(file), "sha256": hashlib.sha256(body.encode()).hexdigest()}})


def test_controlled_file_publication_preserves_full_body_and_requires_review(tmp_path, monkeypatch):
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    result = request(service, writer, file, body)
    assert result["ok"], result
    channel = project_channel(state, "ch-file")
    assert channel["discussions"]["main"]["state"] == "phase3_synthesis"
    message = next(m for m in channel["messages"] if m["refs"].get("synthesis_request_id"))
    selected = message["refs"]["prd_source_file"]
    file.write_text("Changed after submitting")
    assert hydrate_sidecar_ref(state, selected).payload == body
    reply_request = channel["reply_requests"][-1]
    prompt = channel_reply_response_contract(channel, reply_request, message)
    assert selected["ref"] in prompt and "read_only" in prompt
    synthesis = {"summary": "Durable behavior", "decisions": ["Preserve full source"],
                 "prd_file": {k: selected[k] for k in ("ref", "sha256")},
                 "requirement_coverage": [{"id": "REQ-1", "section": "Behavior", "source_refs": [str(file)],
                                           "acceptance_ids": ["AC-1"], "status": "covered"}],
                 "acceptance_criteria": [{"id": "AC-1", "criterion": "Full source is preserved."}], "decision_log": []}
    emit_structured_reply_events(state_dir=state, writer=writer, channel=channel, request=reply_request,
                                 message=message, reply=json.dumps({"channel_synthesis": synthesis}),
                                 reply_event_id="reply-file", actor="synth", source="test")
    events = writer.event_log.read_all()
    proposed = [e.payload for e in events if e.type == "channel.synthesis.proposed"]
    assert len(proposed) == 1, [(e.type, e.payload.get("contract_error")) for e in events]
    prd = json.loads((state / proposed[0]["artifact_ref"]).read_text())
    assert prd["body"]["markdown"].startswith(body.rstrip())
    consensus = next(e.payload for e in events if e.type == "channel.consensus.proposed")
    assert {"dba", "critic"} <= set(consensus["required_signers"])
    assert not any(e.type in {"channel.consensus.reached", "task.created", "workflow.started"} for e in events)
    # Replayed completion is idempotent, even when its source path changed.
    emit_structured_reply_events(state_dir=state, writer=writer, channel=project_channel(state, "ch-file"),
                                 request=reply_request, message=message, reply=json.dumps({"channel_synthesis": synthesis}),
                                 reply_event_id="reply-file", actor="synth", source="test")
    assert len([e for e in writer.event_log.read_all() if e.type == "channel.synthesis.proposed"]) == 1

    # Exercise the actual review reactor and fake provider transport; do not forge signatures.
    import time
    from types import SimpleNamespace
    from zf.core.config.schema import ZfConfig, ChannelConfig
    from zf.runtime.channel_synthesis_reactor import react_channel_consensus_proposed
    from zf.runtime.channel_discussion import advance_discussion
    host = SimpleNamespace(state_dir=state, event_log=writer.event_log, event_writer=writer,
                           project_root=tmp_path, config=ZfConfig(channel=ChannelConfig(semantic_source_max_chars=1_000_000)),
                           openclaw_client=None)
    proposal = next(e for e in events if e.type == "channel.consensus.proposed")
    react_channel_consensus_proposed(host, proposal)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = project_channel(state, "ch-file")
        if {"synth", "dba", "critic"} <= set(current["consensus"]["main"]["signed"]):
            break
        time.sleep(0.02)
    assert {"synth", "dba", "critic"} <= set(current["consensus"]["main"]["signed"])
    advance_discussion(state, writer, channel_id="ch-file", thread_id="main")
    assert not any(e.type == "channel.consensus.reached" for e in writer.event_log.read_all())
    requested = writer.emit("runtime.action.requested", actor="web", payload={"action": "channel-consensus-confirm"})
    confirmed = service.execute(action="channel-consensus-confirm", requested_action="channel-consensus-confirm",
                                requested=requested, payload={"channel_id": "ch-file", "thread_id": "main",
                                "artifact_ref": proposed[0]["artifact_ref"], "artifact_digest": proposed[0]["artifact_digest"],
                                "prd_revision": 1})
    assert confirmed["ok"], confirmed
    advance_discussion(state, writer, channel_id="ch-file", thread_id="main")
    assert project_channel(state, "ch-file")["discussions"]["main"]["state"] == "idle"
    assert not any(e.type in {"task.created", "workflow.started"} for e in writer.event_log.read_all())


@pytest.mark.parametrize("fault", ["changed", "outside", "unreferenced", "open_question", "active", "non_markdown"])
def test_selected_file_rejects_unsafe_or_stale_input_before_request(tmp_path, monkeypatch, fault):
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    if fault == "changed": file.write_text("changed")
    if fault == "outside": file = tmp_path.parent / "other.md"
    if fault == "unreferenced": file = tmp_path / "undeclared.md"; file.write_text(body)
    if fault == "open_question":
        writer.emit("channel.question.opened", actor="test", payload={"channel_id": "ch-file", "thread_id": "main",
                    "question_id": "q", "question": "Owner decision", "kind": "owner_decision", "status": "open"})
    if fault == "active":
        writer.emit("channel.agent.reply.requested", actor="test", payload={"channel_id": "ch-file", "thread_id": "main",
                    "request_id": "busy", "target_member_id": "critic", "message_id": "busy"})
    if fault == "non_markdown":
        file = tmp_path / "data.json"; file.write_text(body)
        writer.emit("channel.message.posted", actor="test", payload={"channel_id": "ch-file", "message_id": "json",
                    "text": f"[JSON]({file})"})
    result = request(service, writer, file, body)
    assert result["ok"] is False, result
    assert not any(e.type == "channel.synthesis.requested" for e in writer.event_log.read_all())


@pytest.mark.parametrize("fault", ["unbound", "digest", "ref", "mixed", "tampered", "thread"])
def test_file_contract_cannot_read_arbitrary_or_changed_sources(tmp_path, monkeypatch, fault):
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    assert request(service, writer, file, body)["ok"]
    event = next(e for e in writer.event_log.read_all() if e.type == "channel.synthesis.requested")
    selected = event.payload["prd_source_file"]
    req = {"prd_source_file": selected, "synthesis_request_id": event.payload["request_id"], "channel_id": "ch-file", "thread_id": "main"}
    synthesis = {"prd_file": {k: selected[k] for k in ("ref", "sha256")}}
    if fault == "unbound": req["synthesis_request_id"] = "unknown"
    if fault == "digest": synthesis["prd_file"]["sha256"] = "wrong"
    if fault == "ref": synthesis["prd_file"]["ref"] = str(file)
    if fault == "mixed": synthesis["prd_markdown"] = "# Short"
    if fault == "tampered": (state / selected["ref"]).write_text("tampered")
    if fault == "thread": req["thread_id"] = "other"
    _, error = expand_prd_file(synthesis, request=req, state_dir=state)
    assert error


def test_file_submission_does_not_silently_reuse_another_active_source(tmp_path, monkeypatch):
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    assert request(service, writer, file, body)["ok"]
    result = request(service, writer, file, body)
    assert not result["ok"] and result["_status_code"] == 409
    assert len([e for e in writer.event_log.read_all() if e.type == "channel.synthesis.requested"]) == 1


def test_repair_keeps_the_original_immutable_file_selection(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zf.runtime.channel_synthesis_reactor import react_channel_synthesis_repair_requested
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    assert request(service, writer, file, body)["ok"]
    original = next(e for e in writer.event_log.read_all() if e.type == "channel.synthesis.requested")
    repair = writer.emit("channel.synthesis.repair.requested", actor="test", payload={
        "channel_id": "ch-file", "thread_id": "main", "request_id": original.payload["request_id"],
        "target_member_id": "synth", "repair_id": "repair-file", "repair_revision": 1,
        "contract_error": "missing requirement coverage"})
    routed = []
    monkeypatch.setattr("zf.runtime.channel_synthesis_reactor._route_runtime_message", lambda host, event, message: routed.append(message))
    host = SimpleNamespace(state_dir=state, event_log=writer.event_log, event_writer=writer)
    react_channel_synthesis_repair_requested(host, repair)
    assert routed[0].payload["refs"]["prd_source_file"] == original.payload["prd_source_file"]
    assert routed[0].payload["refs"]["prd_document_contract"] == "full.v1"
