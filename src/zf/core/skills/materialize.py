"""Materialize enabled role skills into runtime/provider directories."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.skills.provenance import (
    _display_path,
    _format_collision_candidates,
    _normalize_dependency_name,
    _sha256,
    read_skill_metadata,
    resolve_skill,
)
from zf.core.state.atomic_io import atomic_write_text


@dataclass(frozen=True)
class MaterializedSkill:
    name: str
    source: str | None
    source_name: str | None
    materialized_to: str | None
    sha256: str | None
    description: str | None
    status: str
    collision_candidates: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    stages: tuple[str, ...] = field(default_factory=tuple)
    roles: tuple[str, ...] = field(default_factory=tuple)
    backends: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    dependency_of: tuple[str, ...] = field(default_factory=tuple)
    auto_inject: bool = False
    load_on_demand: bool = True


@dataclass(frozen=True)
class SkillMaterializationResult:
    role: str
    instance_id: str
    backend: str
    task_id: str | None
    mode: str
    manifest_path: str
    generated_at: str
    skills: tuple[MaterializedSkill, ...]

    def materialized_paths(self) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for skill in self.skills:
            if skill.materialized_to:
                paths[skill.name] = Path(skill.materialized_to)
        return paths

    def materialized_paths_under(self, project_root: Path) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for skill in self.skills:
            if not skill.materialized_to:
                continue
            path = Path(skill.materialized_to)
            paths[skill.name] = path if path.is_absolute() else project_root / path
        return paths

    def to_payload(self) -> dict:
        return {
            "role": self.role,
            "instance_id": self.instance_id,
            "backend": self.backend,
            "task_id": self.task_id,
            "mode": self.mode,
            "manifest_path": self.manifest_path,
            "generated_at": self.generated_at,
            "skills": [asdict(skill) for skill in self.skills],
        }


def materialize_role_skills(
    *,
    config: ZfConfig,
    project_root: Path,
    state_dir: Path,
    role: RoleConfig,
    task_id: str | None = None,
) -> SkillMaterializationResult | None:
    if not role.skills:
        return None

    mode = config.runtime.skills.materialize
    target_root = _target_root_for_role(state_dir, role)
    manifest_path = (
        state_dir
        / "workdirs"
        / role.instance_id
        / "runtime"
        / "skills-manifest.json"
    )
    target_root.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    materialized: list[MaterializedSkill] = []
    for skill, dependency_of in _expanded_role_skill_requests(
        config=config,
        project_root=project_root,
        state_dir=state_dir,
        role=role,
    ):
        resolution = resolve_skill(
            project_root=project_root,
            state_dir=state_dir,
            name=skill,
            config=config,
        )
        if resolution.path is None:
            materialized.append(MaterializedSkill(
                name=skill,
                source=None,
                source_name=None,
                materialized_to=None,
                sha256=None,
                description=None,
                status="missing",
                dependency_of=tuple(dependency_of),
            ))
            continue

        metadata = read_skill_metadata(resolution.path, expected_name=skill)
        collision_candidates = _format_collision_candidates(
            resolution=resolution,
            project_root=project_root,
        )
        dest = target_root / skill
        _replace_projection(
            source_dir=resolution.path.parent,
            dest_dir=dest,
            mode=mode,
        )
        materialized.append(MaterializedSkill(
            name=skill,
            source=_display_path(resolution.path, project_root),
            source_name=resolution.source_name,
            materialized_to=_display_path(dest, project_root),
            sha256=_sha256(resolution.path),
            description=metadata.description or None,
            status="invalid" if metadata.warnings else "resolved",
            collision_candidates=collision_candidates,
            warnings=metadata.warnings,
            stages=metadata.stages,
            roles=metadata.roles,
            backends=metadata.backends,
            tags=metadata.tags,
            dependencies=metadata.dependencies,
            dependency_of=tuple(dependency_of),
            auto_inject=metadata.auto_inject,
            load_on_demand=metadata.load_on_demand,
        ))

    result = SkillMaterializationResult(
        role=role.name,
        instance_id=role.instance_id,
        backend=role.backend,
        task_id=task_id,
        mode=mode,
        manifest_path=_display_path(manifest_path, project_root),
        generated_at=datetime.now(timezone.utc).isoformat(),
        skills=tuple(materialized),
    )
    atomic_write_text(
        manifest_path,
        json.dumps(result.to_payload(), ensure_ascii=False, indent=2) + "\n",
    )
    return result


def _expanded_role_skill_requests(
    *,
    config: ZfConfig,
    project_root: Path,
    state_dir: Path,
    role: RoleConfig,
) -> list[tuple[str, tuple[str, ...]]]:
    requests: list[tuple[str, tuple[str, ...]]] = []
    queue: list[tuple[str, tuple[str, ...]]] = [
        (_normalize_dependency_name(skill), ())
        for skill in role.skills
        if str(skill).strip()
    ]
    seen: set[str] = set()
    while queue:
        skill, dependency_of = queue.pop(0)
        if skill in seen:
            continue
        seen.add(skill)
        requests.append((skill, dependency_of))
        resolution = resolve_skill(
            project_root=project_root,
            state_dir=state_dir,
            name=skill,
            config=config,
        )
        if resolution.path is None:
            continue
        metadata = read_skill_metadata(resolution.path, expected_name=skill)
        for dependency in metadata.dependencies:
            queue.append((
                _normalize_dependency_name(dependency),
                (*dependency_of, skill),
            ))
    return requests


def _target_root_for_role(state_dir: Path, role: RoleConfig) -> Path:
    if role.backend == "codex":
        return state_dir / "workdirs" / role.instance_id / "codex-home" / "skills"
    if role.backend == "claude-code":
        # Materialize into the worktree's project-level `.claude/skills/`,
        # which Claude Code natively discovers relative to cwd. The old
        # `claude-code/skills/` sibling dir was never loaded: it would only
        # work as `$CLAUDE_CONFIG_DIR/skills`, but the spawn never sets
        # CLAUDE_CONFIG_DIR (claude uses the operator's ~/.claude for
        # auth/trust). Project-level discovery is additive — it does not
        # relocate auth/settings — so no per-role home pre-seed is needed.
        return (
            state_dir / "workdirs" / role.instance_id / "project" / ".claude" / "skills"
        )
    return state_dir / "workdirs" / role.instance_id / "runtime" / "skills"


def _replace_projection(*, source_dir: Path, dest_dir: Path, mode: str) -> None:
    if dest_dir.exists() or dest_dir.is_symlink():
        if dest_dir.is_symlink() or dest_dir.is_file():
            dest_dir.unlink()
        else:
            shutil.rmtree(dest_dir)

    if mode == "symlink":
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        dest_dir.symlink_to(source_dir.resolve(strict=False), target_is_directory=True)
        return

    if mode != "copy":
        raise ValueError(f"unsupported skill materialize mode: {mode!r}")
    shutil.copytree(source_dir, dest_dir)
