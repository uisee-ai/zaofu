"""Channel Skill refs resolve through a state-dir overlay without source dirt."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, SkillSourceConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.channel_skill_context import load_channel_skill_guidance
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_handoff import request_channel_handoff


def _service(
    tmp_path: Path, *, skill_sources: list[SkillSourceConfig] | None = None,
    state_dir_name: str = ".zf",
):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    state_dir = project_root / state_dir_name
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="t", state_dir=state_dir_name),
        skill_sources=skill_sources or [],
    )
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        config=config,
        project_root=project_root,
    )
    return service, log, project_root


def test_channel_review_skill_keeps_complete_package_in_custom_state(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "skills"
    service, _log, root = _service(
        tmp_path, state_dir_name="runtime-data",
        skill_sources=[SkillSourceConfig(name="canonical", path=str(source))],
    )
    _exec(service, "channel-create", {"channel_id": "review", "name": "review"})
    result = _exec(service, "channel-invite-member", {
        "channel_id": "review", "member_id": "architect", "provider": "codex",
        "channel_role": "arch", "skill_refs": ["zf-cr"],
    })
    assert result.get("ok"), result
    bundle = root / "runtime-data/runtime-skills/zf-cr"
    expected = {p.relative_to(source / "zf-cr"): p.read_bytes()
                for p in (source / "zf-cr").rglob("*") if p.is_file()}
    actual = {p.relative_to(bundle): p.read_bytes()
              for p in bundle.rglob("*") if p.is_file()}
    assert actual == expected
    assert not (root / ".zf").exists()
    assert not (root / "skills").exists()


def _exec(service, action: str, payload: dict) -> dict:
    requested = ZfEvent(type="control.action.requested", actor="web", payload=payload)
    return service._execute_action(
        requested=requested, action=action, requested_action=action, payload=payload,
    )


def test_channel_invite_materializes_skill_ref_from_skill_source(tmp_path: Path) -> None:
    skills_repo = tmp_path / "external-skills"
    skill_dir = skills_repo / "zf-channel-discussion-participant"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# participant protocol\n", encoding="utf-8")

    service, log, project_root = _service(
        tmp_path,
        skill_sources=[SkillSourceConfig(name="agent-skills", path=str(skills_repo))],
    )
    _exec(service, "channel-create", {"channel_id": "ch-1", "name": "ch-1"})
    result = _exec(service, "channel-invite-member", {
        "channel_id": "ch-1",
        "member_id": "pm-1",
        "provider": "codex",
        "channel_role": "product_pm",
        "skill_refs": ["zf-channel-discussion-participant"],
        "role_selection_reason": "The requirement needs a product owner view.",
        "requirement_ref": "channels/ch-1/messages/msg-1",
        "requirement_digest": "sha256:requirement-1",
    })

    assert result.get("ok"), result
    invited = next(event for event in log.read_all()
                   if event.type == "channel.member.invited")
    materialized = Path(invited.payload["resolved_skill_refs"][0]["resolved_path"])
    assert materialized.is_file()
    assert materialized.read_text(encoding="utf-8") == "# participant protocol\n"
    assert not (
        project_root
        / "skills"
        / "zf-channel-discussion-participant"
    ).exists()
    invited = next(
        event for event in log.read_all()
        if event.type == "channel.member.invited"
        and event.payload.get("member_id") == "pm-1"
    )
    assert invited.payload["role_selection_reason"] == (
        "The requirement needs a product owner view."
    )
    assert invited.payload["requirement_ref"] == "channels/ch-1/messages/msg-1"
    assert invited.payload["requirement_digest"] == "sha256:requirement-1"
    manifest = (
        project_root / ".zf" / "runtime-skills" / "manifest.json"
    ).read_text(encoding="utf-8")
    assert str(materialized) in manifest


def test_inviting_after_skill_upgrade_preserves_existing_member_method(tmp_path: Path) -> None:
    skills_repo = tmp_path / "external-skills"
    source = skills_repo / "review-method" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("Original review method\n")
    (source.parent / "reference.txt").write_text("Original reference\n")
    service, log, project_root = _service(
        tmp_path, skill_sources=[SkillSourceConfig(name="external", path=str(skills_repo))],
    )
    legacy = project_root / ".zf" / "runtime-skills" / "review-method" / "SKILL.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("Legacy pinned method\n")
    _exec(service, "channel-create", {"channel_id": "ch-1", "name": "ch-1"})
    for member_id in ["before", "after"]:
        if member_id == "after":
            source.write_text("Updated review method\n")
            (source.parent / "reference.txt").write_text("Updated reference\n")
        result = _exec(service, "channel-invite-member", {
            "channel_id": "ch-1", "member_id": member_id, "provider": "codex",
            "channel_role": "critic", "skill_refs": ["review-method"],
        })
        assert result.get("ok"), result
    members = [event.payload for event in log.read_all() if event.type == "channel.member.invited"]
    for member, expected in zip(members, ["Original", "Updated"], strict=True):
        refs = member["resolved_skill_refs"]
        guidance = load_channel_skill_guidance(refs, state_dir=project_root / ".zf", project_root=project_root)
        assert guidance[0]["content"] == f"{expected} review method\n"
        assert Path(refs[0]["resolved_path"]).with_name("reference.txt").read_text() == f"{expected} reference\n"
    assert legacy.read_text() == "Legacy pinned method\n"


def test_channel_invite_skips_materialization_when_already_present(tmp_path: Path) -> None:
    service, _log, project_root = _service(tmp_path)
    existing = project_root / "skills" / "zf-channel-discussion-participant"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("# local copy\n", encoding="utf-8")

    _exec(service, "channel-create", {"channel_id": "ch-1", "name": "ch-1"})
    result = _exec(service, "channel-invite-member", {
        "channel_id": "ch-1",
        "member_id": "pm-1",
        "provider": "codex",
        "channel_role": "product_pm",
        "skill_refs": ["zf-channel-discussion-participant"],
    })

    assert result.get("ok"), result
    # unchanged — the pre-existing local copy is left as-is, not overwritten
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "# local copy\n"
    assert not (
        project_root
        / ".zf"
        / "runtime-skills"
        / "zf-channel-discussion-participant"
    ).exists()


@pytest.mark.parametrize("entrypoint", ["message", "handoff"])
def test_skill_drift_rejection_names_the_skill_instead_of_the_profile(tmp_path: Path, entrypoint: str) -> None:
    service, log, project_root = _service(tmp_path)
    source = project_root / "skills" / "review-method" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("Pinned method\n")
    _exec(service, "channel-create", {"channel_id": "ch-1", "name": "ch-1"})
    result = _exec(service, "channel-invite-member", {
        "channel_id": "ch-1", "member_id": "reviewer", "provider": "codex",
        "channel_role": "critic", "skill_refs": ["review-method"],
    })
    assert result.get("ok"), result
    source.write_text("Changed method\n")
    writer = EventWriter(log)
    message = writer.emit("channel.message.posted", actor="web", payload={
        "channel_id": "ch-1", "thread_id": "main", "message_id": "msg-review",
        "member_id": "operator", "role": "user", "text": "@reviewer review",
        "refs": {"synthesis_request_id": "s1"},
    })
    args = dict(state_dir=project_root / ".zf", writer=writer, actor="web", source="web", project_root=project_root)
    if entrypoint == "message":
        result = route_channel_message(**args, message_event=message, message_payload=message.payload)
        event_type = "channel.context_pack.rejected"
    else:
        result = request_channel_handoff(**args, channel_id="ch-1", thread_id="main",
            message_id="msg-review", member_id="operator", target_member_id="reviewer", reason="review")
        event_type = "channel.handoff.failed"
    assert result.reply_requests == []
    assert result.skipped == [{"target_member_id": "reviewer", "reason": "skill_guidance_hash_mismatch"}]
    rejected = [event for event in log.read_all() if event.type == event_type]
    assert rejected[-1].payload["reason"] == "skill_guidance_hash_mismatch"


def test_channel_invite_without_skill_refs_materializes_nothing(tmp_path: Path) -> None:
    service, _log, project_root = _service(tmp_path)
    _exec(service, "channel-create", {"channel_id": "ch-1", "name": "ch-1"})
    result = _exec(service, "channel-invite-member", {
        "channel_id": "ch-1",
        "member_id": "arch-1",
        "provider": "codex",
        "channel_role": "arch",
    })

    assert result.get("ok"), result
    assert not (project_root / "skills").exists()


def test_channel_invite_rejects_unresolved_skill_ref(tmp_path: Path) -> None:
    service, log, _project_root = _service(tmp_path)
    _exec(service, "channel-create", {"channel_id": "ch-1", "name": "ch-1"})

    result = _exec(service, "channel-invite-member", {
        "channel_id": "ch-1",
        "member_id": "arch-1",
        "provider": "codex",
        "channel_role": "arch",
        "skill_refs": ["does-not-exist"],
    })

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert "skills/does-not-exist/SKILL.md" in result["reason"]
    assert any(
        event.type == "channel.member.add.rejected"
        and event.payload.get("member_id") == "arch-1"
        for event in log.read_all()
    )
    assert not any(
        event.type == "channel.member.invited"
        and event.payload.get("member_id") == "arch-1"
        for event in log.read_all()
    )


def test_runtime_skill_overlay_keeps_target_git_worktree_clean(
    tmp_path: Path,
) -> None:
    skills_repo = tmp_path / "external-skills"
    skill_dir = skills_repo / "review-method"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# review method\n",
        encoding="utf-8",
    )
    service, _log, project_root = _service(
        tmp_path,
        skill_sources=[
            SkillSourceConfig(name="external", path=str(skills_repo)),
        ],
    )
    (project_root / ".gitignore").write_text(".zf/\n", encoding="utf-8")
    (project_root / "README.md").write_text("# target\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        ["git", "add", ".gitignore", "README.md"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ZaoFu Test",
            "-c",
            "user.email=zf@example.test",
            "commit",
            "-qm",
            "init",
        ],
        cwd=project_root,
        check=True,
    )
    _exec(
        service,
        "channel-create",
        {"channel_id": "ch-clean", "name": "ch-clean"},
    )
    result = _exec(service, "channel-invite-member", {
        "channel_id": "ch-clean",
        "member_id": "reviewer",
        "provider": "codex",
        "channel_role": "critic",
        "skill_refs": ["review-method"],
    })

    assert result.get("ok"), result
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
