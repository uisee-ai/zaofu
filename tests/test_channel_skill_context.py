"""Typed provider turns receive pinned methods without filesystem tools."""
import hashlib
from pathlib import Path

import pytest

from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_provider_prompt import build_channel_provider_prompt
from zf.runtime.channel_skill_context import load_channel_skill_guidance
from zf.runtime.sidecar_refs import SidecarRefError


def binding(tmp_path, content="Use stable IDs and define each criterion once."):
    state = tmp_path / "state"
    path = state / "runtime-skills" / "author-method" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    ref = {"logical_ref": "skills/author-method/SKILL.md", "resolved_path": str(path),
           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return state, path, ref


def test_context_pack_and_actual_provider_prompt_include_frozen_method(tmp_path, monkeypatch):
    state, path, ref = binding(tmp_path)
    channel = {"channel_id": "ch", "messages": [{"message_id": "trigger", "thread_id": "main",
        "member_id": "operator", "text": "Synthesize", "refs": {"synthesis_request_id": "s1"}}]}
    pack = build_channel_context_pack(channel, channel_id="ch", thread_id="main",
        target_member_id="synthesizer", trigger_message_id="trigger", state_dir=state,
        project_root=tmp_path, resolved_skill_refs=[ref])
    assert pack["skill_guidance"][0]["content"] == path.read_text()
    assert pack["skill_guidance"][0]["sha256"] == ref["sha256"]
    # Replays consume the immutable pack, not the now changed skill file.
    path.write_text("A different later method.")
    monkeypatch.setattr("zf.runtime.channel_provider_prompt.channel_context_pack_by_id", lambda *a, **k: pack)
    prompt = build_channel_provider_prompt(channel=channel, member={"member_id": "synthesizer"},
        request={"context_pack_id": pack["context_pack_id"]},
        message={"refs": {"synthesis_request_id": "s1"}}, state_dir=state)
    assert "Use stable IDs and define each criterion once." in prompt
    assert "A different later method." not in prompt
    assert "Do not call tools" in prompt
    assert "Apply the supplied context_pack.skill_guidance" in prompt


@pytest.mark.parametrize("fault,code", [("drift", "hash_mismatch"), ("missing", "ref_missing"),
                                      ("outside", "ref_invalid"), ("budget", "budget_exceeded")])
def test_method_loading_fails_closed_before_provider_work(tmp_path, fault, code):
    state, path, ref = binding(tmp_path)
    if fault == "drift": path.write_text("Changed")
    if fault == "missing": path.unlink()
    if fault == "outside": ref["resolved_path"] = str(tmp_path / "private.txt")
    with pytest.raises(SidecarRefError) as error:
        load_channel_skill_guidance([ref], state_dir=state, project_root=tmp_path,
                                    max_bytes=1 if fault == "budget" else 65536)
    assert error.value.code == code


def test_project_owned_skill_and_legacy_empty_refs_remain_supported(tmp_path):
    path = tmp_path / "skills" / "author-method" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("Project method")
    ref = {"logical_ref": "skills/author-method/SKILL.md", "resolved_path": str(path),
           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert load_channel_skill_guidance([ref], state_dir=tmp_path / "state", project_root=tmp_path)[0]["content"] == "Project method"
    assert load_channel_skill_guidance([], state_dir=tmp_path / "state", project_root=None) == []


@pytest.mark.parametrize("skill,refs", [
    ("zf-channel-discussion-synthesizer", {"synthesis_request_id": "s1"}),
    ("zf-channel-discussion-participant", {"consensus_review_id": "r1"}),
])
def test_canonical_quotation_method_reaches_actual_typed_provider_prompt(tmp_path, monkeypatch, skill, refs):
    root = Path(__file__).resolve().parents[1]
    path = root / "skills" / skill / "SKILL.md"
    body = path.read_text()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    channel = {"channel_id": "ch", "messages": [{"message_id": "trigger", "thread_id": "main",
        "member_id": "operator", "text": "Review the original quotation requirement.", "refs": refs}]}
    pack = build_channel_context_pack(channel, channel_id="ch", thread_id="main",
        target_member_id="synthesizer", trigger_message_id="trigger", state_dir=tmp_path,
        project_root=root, resolved_skill_refs=[{"logical_ref": f"skills/{skill}/SKILL.md",
                                                 "resolved_path": str(path), "sha256": digest}])
    assert pack["skill_guidance"][0]["content"] == body
    monkeypatch.setattr("zf.runtime.channel_provider_prompt.channel_context_pack_by_id", lambda *a, **k: pack)
    prompt = build_channel_provider_prompt(channel=channel, member={"member_id": "synthesizer"},
        request={"context_pack_id": pack["context_pack_id"]}, message={"refs": refs}, state_dir=tmp_path)
    assert "直接引用" in prompt or "原文引文" in prompt
    assert pack["skill_guidance"][0]["sha256"] == digest
    for provider in [".codex", ".claude"]:
        distribution = root / provider / "skills" / skill / "SKILL.md"
        if distribution.exists():
            assert distribution.read_bytes() == path.read_bytes()
