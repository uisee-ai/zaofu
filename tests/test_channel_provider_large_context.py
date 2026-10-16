"""Large Channel prompts retain their verified source without inline RPC overflow."""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from zf.runtime.channel_provider_prompt import build_channel_provider_prompt
from zf.runtime.channel_sidecar import channel_context_pack_event_payload
from zf.runtime.sidecar_refs import SidecarRefError


def make_prompt(state, envelope):
    return build_channel_provider_prompt(
        channel={"channel_id": "ch-large", "context_packs": [envelope]},
        member={"member_id": "synthesizer", "permission_profile": "read_only"},
        message={"refs": {"synthesis_request_id": "s1"}},
        request={"context_pack_id": "ctx-large"}, state_dir=state,
    )


def source(state, repeats):
    body = "原文完整内容。" * repeats + "END-OF-ALL-SOURCES"
    pack = {
        "channel_id": "ch-large", "context_pack_id": "ctx-large", "thread_id": "main",
        "semantic_source_required": True, "semantic_source_complete": True,
        "semantic_source_documents": [{"content": body, "message_id": "m1"}],
        "semantic_source_manifest": [{"message_id": "m1", "message_body_digest": "bound-digest"}],
    }
    return pack, channel_context_pack_event_payload(state, pack, created_by="test")


def test_large_prompt_points_to_complete_verified_custom_state_snapshot(tmp_path):
    state = tmp_path / "custom state"
    pack, envelope = source(state, 150_000)
    path = state / envelope["context_pack_ref"]
    original = path.read_bytes()
    prompt = make_prompt(state, envelope)
    assert len(prompt) < 100_000
    view = ast.literal_eval(next(line.removeprefix("context_pack: ")
                                 for line in prompt.splitlines() if line.startswith("context_pack: ")))
    assert Path(view["complete_context_file"]) == path.resolve()
    assert view["context_pack_sha256"].removeprefix("sha256:") == hashlib.sha256(original).hexdigest()
    delivered = json.loads(Path(view["complete_context_file"]).read_text())
    assert delivered["semantic_source_documents"] == pack["semantic_source_documents"]
    assert delivered["semantic_source_manifest"] == pack["semantic_source_manifest"]
    assert path.read_bytes() == original
    assert "Do not call tools" not in prompt
    assert "read-only tools only to read and verify complete_context_file" in prompt
    assert "do not attest all_required_sources_read" in prompt


def test_small_prompt_keeps_existing_inline_and_tool_constraints(tmp_path):
    pack, envelope = source(tmp_path, 3)
    prompt = make_prompt(tmp_path, envelope)
    assert pack["semantic_source_documents"][0]["content"] in prompt
    assert "complete_context_file" not in prompt
    assert "Do not call tools" in prompt


def test_file_path_comes_from_verified_descriptor_not_conflicting_projection(tmp_path):
    _, envelope = source(tmp_path, 150_000)
    verified_path = (tmp_path / envelope["context_pack_ref"]).resolve()
    envelope["context_pack_ref"] = "../unverified.json"
    prompt = make_prompt(tmp_path, envelope)
    assert str(verified_path) in prompt
    assert "unverified.json" not in prompt


def test_headless_dispatch_delivers_large_file_without_expanding_permissions(tmp_path):
    from zf.core.events import EventLog, EventWriter
    from zf.runtime.channel_adapter import _run_headless_reply
    from zf.web.headless_agent import HeadlessTurnResult

    state = tmp_path / "runtime"
    pack, envelope = source(state, 150_000)
    calls = []

    class Backend:
        def available(self):
            return True

        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return HeadlessTurnResult(
                ok=False, status="failed", backend="codex-headless",
                thread_id=kwargs["thread_id"], provider_session_id="", reply="",
                messages=[], usage={}, error="test stops after verifying input",
            )

    _run_headless_reply(
        state_dir=state, project_root=tmp_path, writer=EventWriter(EventLog(state / "events.jsonl")),
        channel={"channel_id": "ch-large", "context_packs": [envelope]},
        member={"member_id": "synthesizer", "backend": "codex", "permission_profile": "read_only"},
        message={"text": "Synthesize", "refs": {"synthesis_request_id": "s1"}},
        request={"request_id": "r1", "context_pack_id": "ctx-large", "thread_id": "main"},
        request_id="r1", started_event_id="start", actor="test", source="test",
        backend="codex-headless", backends={"codex-headless": Backend()},
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["permission_profile"] == "read_only"
    assert call["provider_session_id"] == ""
    assert len(call["prompt"]) < 100_000
    view = ast.literal_eval(next(line.removeprefix("context_pack: ")
                                 for line in call["prompt"].splitlines() if line.startswith("context_pack: ")))
    assert json.loads(Path(view["complete_context_file"]).read_text())["semantic_source_documents"] == pack["semantic_source_documents"]


@pytest.mark.parametrize("fault", ["missing", "changed", "outside"])
def test_file_delivery_does_not_bypass_sidecar_verification(tmp_path, fault):
    _, envelope = source(tmp_path, 150_000)
    path = tmp_path / envelope["context_pack_ref"]
    if fault == "missing":
        path.unlink()
    elif fault == "changed":
        path.write_text("{}")
    else:
        envelope["refs"]["context_pack"]["ref"] = "../outside.json"
    with pytest.raises(SidecarRefError):
        make_prompt(tmp_path, envelope)
