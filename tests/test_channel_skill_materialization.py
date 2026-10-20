"""Channel Skill refs resolve through a state-dir overlay without source dirt."""

from __future__ import annotations

import subprocess
from pathlib import Path

from zf.core.config.schema import ProjectConfig, SkillSourceConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService


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
    materialized = (
        project_root
        / ".zf"
        / "runtime-skills"
        / "zf-channel-discussion-participant"
        / "SKILL.md"
    )
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
