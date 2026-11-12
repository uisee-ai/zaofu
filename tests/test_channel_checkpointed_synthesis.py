"""Interrupted pieces resume from verified receipts without bypassing publication."""
import json
import re
from dataclasses import replace

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.runtime.channel_checkpointed_synthesis import run_checkpointed_synthesis
from zf.runtime.channel_synthesis_parts import PARTS
from zf.runtime.channel_synthesis_checkpoints import binding_for, load_parts
from zf.runtime.channel_synthesis_checkpoints import save_part, fingerprint
from zf.runtime.sidecar_refs import SidecarRefError
from zf.web.headless_agent import HeadlessMessage, HeadlessTurnResult


@pytest.fixture
def world(tmp_path, monkeypatch):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    pack = {"channel_id": "ch", "thread_id": "main", "context_pack_id": "ctx",
            "context_pack_sha256": "c" * 64, "semantic_source_complete": True,
            "semantic_source_manifest_digest": "sources", "question_ledger_digest": "answers"}
    monkeypatch.setattr("zf.runtime.channel_checkpointed_synthesis.channel_context_pack_by_id", lambda *a, **k: dict(pack))
    member = {"member_id": "synth", "backend": "codex", "channel_role": "synthesizer", "permission_profile": "read_only"}
    def request(identity, parent="", digest="i" * 64):
        snapshot = {"sha256": digest}
        writer.emit("channel.synthesis.requested", actor="web", payload={"channel_id": "ch", "thread_id": "main",
                    "request_id": identity, "target_member_id": "synth", "prd_input_snapshot": snapshot,
                    **({"retry_of_request_id": parent} if parent else {})})
        message = {"refs": {"synthesis_request_id": identity, "synthesis_pipeline": "checkpointed.v1",
                            "prd_input_snapshot": snapshot}}
        stream = AgentSessionStreamEmitter(writer=writer, identity=AgentSessionIdentity(run_id=f"run-{identity}",
                    thread_id="main", source="channel.headless", actor="runtime", channel_id="ch",
                    target_member_id="synth", request_id=identity))
        stream.start()
        return message, lambda a: stream.emit_message(HeadlessMessage(type="status", content=a["title"], raw={"activity": a}))
    return tmp_path, writer, pack, member, request


def fields(spec, pack):
    value = {**{k: [] for k in spec.arrays}, **{k: {} for k in spec.objects}, **{k: "value" for k in spec.strings}}
    if spec.name == "document":
        value.update(title="Game", summary="Single-player game", spec_format="prd-spec.v1",
                     prd_markdown="# Game\n\nComplete candidate document", document_control={"revision": "2", "status": "draft", "owner": "Owner", "profile": "full"})
        value["success_metrics"] = [{"id": "SM-1", "statement": "A user can complete a game.",
            "baseline": "TBD", "target": "TBD", "measurement_method": "Observe a complete game",
            "owner": "Owner", "measurement_timing": "Acceptance", "status": "NOT_RUN"}]
    if spec.name == "finalize":
        value.update(quality_contract="channel-prd-quality.v1", source_attestation={"context_pack_id": pack["context_pack_id"],
                     "context_pack_sha256": pack["context_pack_sha256"], "all_required_sources_read": True})
    return value


def backend(pack, calls, *, fail="", malformed=""):
    def turn(prompt, session):
        name = re.search(r"ONLY part '([^']+)'", prompt).group(1)
        calls.append((name, prompt))
        result = HeadlessTurnResult(ok=True, status="completed", backend="codex", thread_id="main",
                                    provider_session_id="native", reply="", messages=[], usage={"output_tokens": 2})
        if name == fail:
            return replace(result, ok=False, status="failed", error="codex turn timed out at total deadline",
                           telemetry={"input_delivery": "unknown"})
        spec = next(s for s in PARTS if s.name == name)
        value = fields(spec, pack)
        if name == malformed:
            return replace(result, reply='{"channel_synthesis_part": {')
        return replace(result, reply="Working on this portion.\n<!-- zf:channel-contract -->\n" + json.dumps({
            "channel_synthesis_part": {"part": name, "fields": value}}))
    return turn


def run(world, message, progress, turn, active=lambda: True):
    state, writer, pack, member, _ = world
    return run_checkpointed_synthesis(run_turn=turn, initial_prompt="BOUND COMPLETE SOURCES\nResponse contract (authoritative for this reply):\nOLD FULL CONTRACT",
                provider_session_id="", channel={"channel_id": "ch"}, member=member,
                request={"thread_id": "main", "context_pack_id": "ctx"}, message=message,
                state_dir=state, on_progress=progress, is_active=active)


def test_explicit_retry_preserves_completed_parts_and_rebinds_final_attestation(world):
    state, writer, pack, member, request = world
    message, progress = request("first")
    calls = []
    result = run(world, message, progress, backend(pack, calls, fail="requirements"))
    assert not result.ok and [c[0] for c in calls] == ["document", "requirements"]
    assert result.telemetry["input_delivery"] == "unknown"
    writer.emit("channel.synthesis.cancelled", actor="web", payload={"channel_id": "ch", "thread_id": "main", "request_id": "first"})
    pack.update(context_pack_id="ctx-retry", context_pack_sha256="d" * 64)
    retry, progress = request("retry", "first")
    calls = []
    result = run(world, retry, progress, backend(pack, calls))
    assert result.ok
    assert [c[0] for c in calls] == ["requirements", "decisions", "implementation", "finalize"]
    assert "BOUND COMPLETE SOURCES" in calls[0][1] and "OLD FULL CONTRACT" not in calls[0][1]
    contract = json.loads(result.reply.split("<!-- zf:channel-contract -->\n")[1])["channel_synthesis"]
    assert contract["source_attestation"]["context_pack_id"] == "ctx-retry"
    assert not any(e.type in {"channel.synthesis.proposed", "task.created", "workflow.started"} for e in writer.event_log.read_all())


def test_new_material_or_unrelated_request_cannot_reuse_cached_work(world):
    state, writer, pack, member, request = world
    original, progress = request("first")
    run(world, original, progress, backend(pack, [], fail="requirements"))
    for identity, parent, digest in (("fresh", "", "i" * 64), ("changed", "first", "j" * 64)):
        message, progress = request(identity, parent, digest)
        calls = []
        run(world, message, progress, backend(pack, calls))
        assert calls[0][0] == "document"


def test_invalid_part_retries_only_that_part_and_does_not_save_it(world):
    state, writer, pack, member, request = world
    message, progress = request("first")
    calls = []
    result = run(world, message, progress, backend(pack, calls, malformed="requirements"))
    assert not result.ok
    assert [c[0] for c in calls] == ["document", "requirements", "requirements"]
    saved = load_parts(state, binding=binding_for(pack, member, message), request_id="first", context_digest=pack["context_pack_sha256"])
    assert set(saved) == {"document"}


def test_tampered_saved_part_fails_closed(world):
    state, writer, pack, member, request = world
    message, progress = request("first")
    run(world, message, progress, backend(pack, [], fail="requirements"))
    artifact = next((state / "channels/synthesis-parts").rglob("document-*.json"))
    artifact.write_text("changed")
    with pytest.raises(SidecarRefError):
        run(world, message, progress, backend(pack, []))


def test_cancelled_turn_cannot_save_its_output(world):
    state, writer, pack, member, request = world
    message, progress = request("first")
    active = [True]
    native = backend(pack, [])
    def turn(prompt, session):
        result = native(prompt, session)
        active[0] = False
        return result
    result = run(world, message, progress, turn, active=lambda: active[0])
    assert not result.ok and result.status == "cancelled"
    assert not list((state / "channels/synthesis-parts").rglob("*.json"))


def test_late_checkpoint_receipt_after_cancellation_is_not_reused(world):
    state, writer, pack, member, request = world
    message, progress = request("first")
    binding = binding_for(pack, member, message)
    descriptor = save_part(state, spec=PARTS[0], fields=fields(PARTS[0], pack), binding=binding,
                           request_id="first", context_digest=pack["context_pack_sha256"])
    writer.emit("channel.synthesis.cancelled", actor="web", payload={"channel_id": "ch", "thread_id": "main", "request_id": "first"})
    progress({"id": "late", "title": "late", "state": "completed", "checkpoint": descriptor,
              "synthesis_request_id": "first", "input_fingerprint": fingerprint(binding)})
    retry, _ = request("retry", "first")
    assert not load_parts(state, binding=binding_for(pack, member, retry), request_id="retry", context_digest=pack["context_pack_sha256"])


def test_role_or_thread_change_cannot_reuse_checkpoint(world):
    state, writer, pack, member, request = world
    message, progress = request("first")
    run(world, message, progress, backend(pack, [], fail="requirements"))
    for changed in ({**member, "channel_role": "critic"}, {**member, "permission_profile": "workspace_writer"}):
        assert not load_parts(state, binding=binding_for(pack, changed, message), request_id="first", context_digest=pack["context_pack_sha256"])
    other = {**pack, "thread_id": "other"}
    assert not load_parts(state, binding=binding_for(other, member, message), request_id="first", context_digest=pack["context_pack_sha256"])
    changed_method = {**pack, "role_definition": "Changed instructions for this role"}
    assert not load_parts(state, binding=binding_for(changed_method, member, message), request_id="first", context_digest=pack["context_pack_sha256"])


def test_strengthened_schema_regenerates_only_invalid_intact_portion(world, monkeypatch):
    state, writer, pack, member, request = world
    message, progress = request("first")
    binding = binding_for(pack, member, message)
    for index, spec in enumerate(PARTS[:-1]):
        value = fields(spec, pack)
        if spec.name == "document":
            value["success_metrics"][0].pop("statement")
        with monkeypatch.context() as previous_schema:
            previous_schema.setattr("zf.runtime.channel_synthesis_checkpoints.part_error", lambda *a: "")
            descriptor = save_part(state, spec=spec, fields=value, binding=binding, request_id="first", context_digest=pack["context_pack_sha256"])
        progress({"id": spec.name, "title": spec.title, "state": "completed", "index": index,
            "synthesis_request_id": "first", "input_fingerprint": fingerprint(binding), "checkpoint": descriptor})
    writer.emit("channel.synthesis.cancelled", actor="web", payload={"channel_id": "ch", "thread_id": "main", "request_id": "first"})
    retry, progress = request("retry", "first")
    calls = []
    result = run(world, retry, progress, backend(pack, calls))
    assert result.ok
    assert [c[0] for c in calls] == ["document", "finalize"]


def test_missing_metric_statement_is_rejected_before_long_mapping_turn(world):
    state, writer, pack, member, request = world
    message, progress = request("first")
    calls = []
    normal = backend(pack, calls)
    def turn(prompt, session):
        result = normal(prompt, session)
        contract = json.loads(result.reply.split("<!-- zf:channel-contract -->\n")[1])
        contract["channel_synthesis_part"]["fields"]["success_metrics"][0].pop("statement")
        return replace(result, reply=json.dumps(contract))
    result = run(world, message, progress, turn)
    assert not result.ok and "requires a statement" in result.error
    assert [c[0] for c in calls] == ["document", "document"]
    assert not list((state / "channels/synthesis-parts").rglob("*.json"))
