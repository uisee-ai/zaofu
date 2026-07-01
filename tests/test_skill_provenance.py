"""Tests for skills provenance lockfile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import ProjectConfig, RoleConfig, SkillSourceConfig, ZfConfig
from zf.core.skills import (
    build_skill_lock_entries,
    materialize_role_skills,
    upsert_skills_lockfile,
    validate_skill_sources,
)


def _write_skill(root: Path, name: str, text: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.startswith("---"):
        text = (
            "---\n"
            f"name: {name}\n"
            f"description: Test skill {name}. Use when testing skill resolution.\n"
            "---\n\n"
            f"{text}"
        )
    path.write_text(text, encoding="utf-8")
    return path


def test_only_configured_skills_enter_lock_entries(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_skill(state_dir / "skills", "enabled", "# Enabled\n")
    _write_skill(state_dir / "skills", "unused", "# Unused\n")
    role = RoleConfig(name="dev", skills=["enabled"])

    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        task_id="T1",
        run_id="sess-1",
    )

    assert [entry.name for entry in entries] == ["enabled"]
    assert entries[0].status == "resolved"
    assert entries[0].source == ".zf/skills/enabled/SKILL.md"
    assert entries[0].sha256 is not None


def test_missing_enabled_skill_becomes_warning_and_missing_entry(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    role = RoleConfig(name="dev", skills=["missing"])
    config = ZfConfig(project=ProjectConfig(name="test"), roles=[role])

    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
    )
    warnings = validate_skill_sources(config=config, project_root=tmp_path)

    assert entries[0].status == "missing"
    assert entries[0].source is None
    assert entries[0].sha256 is None
    assert "missing" in warnings[0]


def test_external_skill_source_resolves_and_records_description(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    external = tmp_path / "agent-skills" / "skills"
    _write_skill(external, "planning-and-task-breakdown", "# Planning\n")
    role = RoleConfig(name="orchestrator", skills=["planning-and-task-breakdown"])
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[role],
        skill_sources=[
            SkillSourceConfig(
                name="agent-skills",
                path=str(external),
                mode="readonly",
            )
        ],
    )

    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        config=config,
    )
    warnings = validate_skill_sources(config=config, project_root=tmp_path)

    assert warnings == []
    assert entries[0].status == "resolved"
    assert entries[0].source_name == "agent-skills"
    assert entries[0].description
    assert entries[0].source == "agent-skills/skills/planning-and-task-breakdown/SKILL.md"


def test_skill_visibility_metadata_is_recorded_and_checked(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_skill(
        tmp_path / "skills",
        "review-only",
        "---\n"
        "name: review-only\n"
        "description: Review scoped skill.\n"
        "stages: [review]\n"
        "roles: [review]\n"
        "backends: [claude-code]\n"
        "tags: [quality]\n"
        "auto_inject: true\n"
        "load_on_demand: false\n"
        "---\n\n"
        "# Review\n",
    )
    role = RoleConfig(
        name="verify",
        backend="codex",
        stages=["verify"],
        skills=["review-only"],
    )
    config = ZfConfig(project=ProjectConfig(name="test"), roles=[role])

    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        config=config,
    )
    warnings = validate_skill_sources(config=config, project_root=tmp_path)

    assert entries[0].status == "resolved"
    assert entries[0].stages == ("review",)
    assert entries[0].roles == ("review",)
    assert entries[0].backends == ("claude-code",)
    assert entries[0].tags == ("quality",)
    assert entries[0].auto_inject is True
    assert entries[0].load_on_demand is False
    assert any("visibility.backends" in item for item in entries[0].routing_warnings)
    assert any("visibility.stages" in item for item in warnings)


def test_enabled_skill_collision_is_reported_and_recorded(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    agent = tmp_path / "agent-skills" / "skills"
    yoke = tmp_path / "yoke" / "role-skills" / "dev-worker"
    _write_skill(agent, "test-driven-development", "# Agent TDD\n")
    _write_skill(yoke, "test-driven-development", "# Yoke TDD\n")
    role = RoleConfig(name="dev", skills=["test-driven-development"])
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[role],
        skill_sources=[
            SkillSourceConfig(
                name="agent-skills",
                path=str(agent),
                mode="readonly",
            ),
            SkillSourceConfig(
                name="yoke-dev-worker",
                path=str(yoke),
                mode="readonly",
            ),
        ],
    )

    warnings = validate_skill_sources(config=config, project_root=tmp_path)
    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        config=config,
    )

    assert any("multiple candidates" in warning for warning in warnings)
    assert "resolved source 'agent-skills'" in warnings[0]
    assert entries[0].source_name == "agent-skills"
    assert entries[0].collision_candidates == (
        "agent-skills:agent-skills/skills/test-driven-development/SKILL.md",
        "yoke-dev-worker:yoke/role-skills/dev-worker/test-driven-development/SKILL.md",
    )


def test_invalid_skill_frontmatter_is_reported(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    bad = state_dir / "skills" / "review" / "SKILL.md"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "---\nname: wrong-name\n---\n\n# Bad\n",
        encoding="utf-8",
    )
    role = RoleConfig(name="review", skills=["review"])
    config = ZfConfig(project=ProjectConfig(name="test"), roles=[role])

    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        config=config,
    )
    warnings = validate_skill_sources(config=config, project_root=tmp_path)

    assert entries[0].status == "invalid"
    assert any("does not match" in w for w in warnings)
    assert any("description" in w for w in warnings)


def test_skill_hash_changes_are_detected(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    skill = _write_skill(tmp_path / "skills", "review", "v1\n")
    role = RoleConfig(name="review", skills=["review"])

    before = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
    )[0]
    skill.write_text("v2\n", encoding="utf-8")
    after = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
    )[0]

    assert before.sha256 != after.sha256


def test_upsert_skills_lockfile_replaces_same_context(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    skill = _write_skill(tmp_path / "skills", "review", "v1\n")
    role = RoleConfig(name="review", skills=["review"])
    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        task_id="T1",
        run_id="sess-1",
    )
    upsert_skills_lockfile(state_dir=state_dir, entries=entries)

    skill.write_text("v2\n", encoding="utf-8")
    entries = build_skill_lock_entries(
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        task_id="T1",
        run_id="sess-1",
    )
    path = upsert_skills_lockfile(state_dir=state_dir, entries=entries)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["skills"]) == 1
    assert data["skills"][0]["sha256"] == entries[0].sha256


def test_materialize_copies_only_enabled_role_skills(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_skill(tmp_path / "skills", "enabled", "# Enabled\n")
    _write_skill(tmp_path / "skills", "unused", "# Unused\n")
    role = RoleConfig(name="dev", backend="codex", skills=["enabled"])
    config = ZfConfig(project=ProjectConfig(name="test"), roles=[role])

    result = materialize_role_skills(
        config=config,
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        task_id="T1",
    )

    assert result is not None
    target = state_dir / "workdirs" / "dev" / "codex-home" / "skills"
    assert (target / "enabled" / "SKILL.md").exists()
    assert not (target / "unused").exists()
    manifest = json.loads(
        (state_dir / "workdirs" / "dev" / "runtime" / "skills-manifest.json")
        .read_text(encoding="utf-8")
    )
    assert manifest["task_id"] == "T1"
    assert manifest["skills"][0]["name"] == "enabled"
    assert manifest["skills"][0]["materialized_to"] == (
        ".zf/workdirs/dev/codex-home/skills/enabled"
    )


def test_materialize_claude_code_targets_worktree_project_dot_claude(tmp_path: Path):
    # claude-code skills must land in the worktree's project-level
    # `.claude/skills/`, which Claude Code natively discovers relative to cwd.
    # (The old `claude-code/skills/` sibling was never loaded — see
    # _target_root_for_role.)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_skill(tmp_path / "skills", "enabled", "# Enabled\n")
    role = RoleConfig(name="dev", backend="claude-code", skills=["enabled"])
    config = ZfConfig(project=ProjectConfig(name="test"), roles=[role])

    result = materialize_role_skills(
        config=config,
        project_root=tmp_path,
        state_dir=state_dir,
        role=role,
        task_id="T1",
    )

    assert result is not None
    target = state_dir / "workdirs" / "dev" / "project" / ".claude" / "skills"
    assert (target / "enabled" / "SKILL.md").exists()
    manifest = json.loads(
        (state_dir / "workdirs" / "dev" / "runtime" / "skills-manifest.json")
        .read_text(encoding="utf-8")
    )
    assert manifest["skills"][0]["materialized_to"] == (
        ".zf/workdirs/dev/project/.claude/skills/enabled"
    )


def test_dev_codex_example_materializes_only_each_role_enabled_skills(
    tmp_path: Path,
):
    example = Path(__file__).parent.parent / "examples" / "dev-codex-backends.yaml"
    if not example.exists():
        pytest.skip("examples/dev-codex-backends.yaml not present")

    config = load_config(example)
    missing_sources = [
        source.path
        for source in config.skill_sources
        if not Path(source.path).expanduser().exists()
    ]
    if missing_sources:
        pytest.skip(f"external skill source missing: {missing_sources}")

    project_root = tmp_path
    state_dir = project_root / config.project.state_dir
    state_dir.mkdir(parents=True)

    warnings = validate_skill_sources(config=config, project_root=project_root)
    assert warnings == []

    for role in config.roles:
        result = materialize_role_skills(
            config=config,
            project_root=project_root,
            state_dir=state_dir,
            role=role,
        )

        assert result is not None
        expected = set(role.skills)
        manifest_path = (
            state_dir
            / "workdirs"
            / role.instance_id
            / "runtime"
            / "skills-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_names = {skill["name"] for skill in manifest["skills"]}
        assert manifest_names == expected, role.instance_id

        target = state_dir / "workdirs" / role.instance_id / "codex-home" / "skills"
        materialized_dirs = {
            path.name
            for path in target.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        }
        assert materialized_dirs == expected, role.instance_id

        for skill in manifest["skills"]:
            assert skill["status"] == "resolved"
            assert skill["materialized_to"].endswith(
                f"/{role.instance_id}/codex-home/skills/{skill['name']}"
            )

    critic_manifest = json.loads(
        (
            state_dir
            / "workdirs"
            / "critic"
            / "runtime"
            / "skills-manifest.json"
        ).read_text(encoding="utf-8")
    )
    critic_sources = {
        skill["name"]: skill["source_name"]
        for skill in critic_manifest["skills"]
    }
    assert critic_sources["document-review"] == "yoke-critic"
    assert critic_sources["plan-option-scoring"] == "yoke-critic"
    assert critic_sources["skeptic-observation"] == "yoke-critic"
    assert critic_sources["security-review"] == "yoke-critic"

    judge_manifest = json.loads(
        (
            state_dir
            / "workdirs"
            / "judge"
            / "runtime"
            / "skills-manifest.json"
        ).read_text(encoding="utf-8")
    )
    judge_sources = {
        skill["name"]: skill["source_name"]
        for skill in judge_manifest["skills"]
    }
    assert judge_sources["final-meta-review"] == "yoke-critic"
    assert judge_sources["pre-release-review"] == "yoke-critic"
