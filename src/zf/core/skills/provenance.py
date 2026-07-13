"""Resolve configured skills and write provenance lockfiles."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


LOCKFILE_NAME = "skills.lock.json"


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    stages: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    backends: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    auto_inject: bool = False
    load_on_demand: bool = True
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillLockEntry:
    role: str
    instance_id: str
    backend: str
    task_id: str | None
    run_id: str | None
    name: str
    source: str | None
    sha256: str | None
    source_name: str | None = None
    description: str | None = None
    materialized_to: str | None = None
    collision_candidates: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    routing_warnings: tuple[str, ...] = field(default_factory=tuple)
    stages: tuple[str, ...] = field(default_factory=tuple)
    roles: tuple[str, ...] = field(default_factory=tuple)
    backends: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    dependency_of: tuple[str, ...] = field(default_factory=tuple)
    auto_inject: bool = False
    load_on_demand: bool = True
    status: str = "resolved"
    # ZF-TR-SKILL-PROVENANCE-001 (doc 39 §2.1.8): provenance fields
    # for yoke / agent-skills overlay tracking. ``override`` is True
    # when the materialized skill diverges from its upstream source;
    # ``last_synced_at`` records the last sync from the canonical
    # source. Reading code that doesn't know these fields stays
    # compatible (frozen dataclass + default values).
    override: bool = False
    last_synced_at: str | None = None


@dataclass(frozen=True)
class SkillCandidate:
    source_name: str
    path: Path


@dataclass(frozen=True)
class SkillResolution:
    name: str
    path: Path | None
    source_name: str | None = None
    collision_candidates: tuple[SkillCandidate, ...] = ()


def resolve_skill_source(
    *,
    project_root: Path,
    state_dir: Path,
    name: str,
    config: ZfConfig | None = None,
) -> Path | None:
    resolution = resolve_skill(
        project_root=project_root,
        state_dir=state_dir,
        name=name,
        config=config,
    )
    return resolution.path


def resolve_skill(
    *,
    project_root: Path,
    state_dir: Path,
    name: str,
    config: ZfConfig | None = None,
) -> SkillResolution:
    name = _normalize_dependency_name(name)
    candidates = find_skill_candidates(
        project_root=project_root,
        state_dir=state_dir,
        name=name,
        config=config,
    )
    if candidates:
        selected = candidates[0]
        return SkillResolution(
            name=name,
            path=selected.path,
            source_name=selected.source_name,
            collision_candidates=candidates,
        )
    return SkillResolution(name=name, path=None)


def find_skill_candidates(
    *,
    project_root: Path,
    state_dir: Path,
    name: str,
    config: ZfConfig | None = None,
) -> tuple[SkillCandidate, ...]:
    """Return all configured candidates matching one enabled skill name.

    Only immediate `<root>/<skill>/SKILL.md` children are considered. This
    matches the resolver's contract and avoids accidentally reactivating
    archived or nested upstream skill copies.
    """
    name = _normalize_dependency_name(name)
    candidate_paths: list[tuple[str, Path]] = [
        ("state", state_dir / "skills" / name / "SKILL.md"),
        ("project", project_root / "skills" / name / "SKILL.md"),
    ]
    if config is not None:
        for source in config.skill_sources:
            root = Path(source.path).expanduser()
            if not root.is_absolute():
                root = project_root / root
            candidate_paths.append((
                source.name,
                root / name / "SKILL.md",
            ))
    candidates = _existing_skill_candidates(candidate_paths)
    if candidates:
        return tuple(candidates)
    return tuple(_existing_skill_candidates(_repo_local_yoke_candidates(project_root, name)))


def _existing_skill_candidates(
    candidate_paths: list[tuple[str, Path]],
) -> list[SkillCandidate]:
    candidates: list[SkillCandidate] = []
    seen: set[Path] = set()
    for source_name, path in candidate_paths:
        if path.is_file():
            resolved = path.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(SkillCandidate(source_name=source_name, path=path))
    return candidates


def _repo_local_yoke_candidates(project_root: Path, name: str) -> list[tuple[str, Path]]:
    """Return explicit yoke overlay candidates for configured/dependent skills.

    Yoke is not a second control plane: these candidates are considered only
    when a role explicitly enables a yoke skill name or a configured skill
    declares it as a dependency. They let role workdirs receive the companion
    method skills referenced by ``zf-yoke-*`` context skills without requiring
    every project zf.yaml to declare another absolute skill source.
    """
    normalized = _normalize_dependency_name(name)
    roots = [
        project_root / "yoke",
        _zaofu_repo_root() / "yoke",
    ]
    candidates: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for root in roots:
        path = root / normalized / "SKILL.md"
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        candidates.append(("yoke", path))
    return candidates


def _zaofu_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def read_skill_metadata(path: Path, *, expected_name: str) -> SkillMetadata:
    warnings: list[str] = []
    text = path.read_text(encoding="utf-8")
    frontmatter = _read_frontmatter(text)
    if frontmatter is None:
        return SkillMetadata(
            name=expected_name,
            description="",
            warnings=("missing required YAML frontmatter",),
        )
    declared_name = str(frontmatter.get("name", "") or "")
    description = str(frontmatter.get("description", "") or "")
    if not declared_name:
        warnings.append("frontmatter.name is required")
        declared_name = expected_name
    if declared_name != expected_name:
        warnings.append(
            f"frontmatter.name {declared_name!r} does not match enabled skill {expected_name!r}"
        )
    if not description:
        warnings.append("frontmatter.description is required")
    return SkillMetadata(
        name=declared_name,
        description=description,
        stages=_string_tuple(frontmatter.get("stages")),
        roles=_string_tuple(frontmatter.get("roles") or frontmatter.get("hats")),
        backends=_string_tuple(frontmatter.get("backends")),
        tags=_string_tuple(frontmatter.get("tags")),
        dependencies=_dependency_tuple(frontmatter),
        auto_inject=_bool_value(frontmatter.get("auto_inject"), default=False),
        load_on_demand=_bool_value(frontmatter.get("load_on_demand"), default=True),
        warnings=tuple(warnings),
    )


def build_skill_lock_entries(
    *,
    project_root: Path,
    state_dir: Path,
    role: RoleConfig,
    config: ZfConfig | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    materialized_paths: dict[str, Path] | None = None,
) -> list[SkillLockEntry]:
    entries: list[SkillLockEntry] = []
    for skill, dependency_of in _expanded_role_skill_requests(
        project_root=project_root,
        state_dir=state_dir,
        role=role,
        config=config,
    ):
        resolution = resolve_skill(
            project_root=project_root,
            state_dir=state_dir,
            name=skill,
            config=config,
        )
        source = resolution.path
        if source is None:
            entries.append(SkillLockEntry(
                role=role.name,
                instance_id=role.instance_id,
                backend=role.backend,
                task_id=task_id,
                run_id=run_id,
                name=skill,
                source=None,
                sha256=None,
                source_name=None,
                dependency_of=tuple(dependency_of),
                status="missing",
            ))
            continue
        metadata = read_skill_metadata(source, expected_name=skill)
        status = "invalid" if metadata.warnings else "resolved"
        collision_candidates = _format_collision_candidates(
            resolution=resolution,
            project_root=project_root,
        )
        materialized_to = None
        if materialized_paths and skill in materialized_paths:
            materialized_to = _display_path(materialized_paths[skill], project_root)
        entries.append(SkillLockEntry(
            role=role.name,
            instance_id=role.instance_id,
            backend=role.backend,
            task_id=task_id,
            run_id=run_id,
            name=skill,
            source=_display_path(source, project_root),
            sha256=_sha256(source),
            source_name=resolution.source_name,
            description=metadata.description or None,
            materialized_to=materialized_to,
            collision_candidates=collision_candidates,
            warnings=metadata.warnings,
            routing_warnings=_skill_routing_warnings(metadata=metadata, role=role),
            stages=metadata.stages,
            roles=metadata.roles,
            backends=metadata.backends,
            tags=metadata.tags,
            dependencies=metadata.dependencies,
            dependency_of=tuple(dependency_of),
            auto_inject=metadata.auto_inject,
            load_on_demand=metadata.load_on_demand,
            status=status,
        ))
    return entries


def upsert_skills_lockfile(
    *,
    state_dir: Path,
    entries: list[SkillLockEntry],
) -> Path:
    path = state_dir / LOCKFILE_NAME
    with locked_path(path):
        existing: list[dict] = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                existing = list(data.get("skills", []) or [])
            except (OSError, json.JSONDecodeError, TypeError):
                existing = []

        keys = {_entry_key(entry) for entry in entries}
        kept = [
            item for item in existing
            if _dict_key(item) not in keys
        ]
        payload = {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "skills": kept + [asdict(entry) for entry in entries],
        }
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
    return path


def validate_skill_sources(
    *,
    config: ZfConfig,
    project_root: Path,
) -> list[str]:
    state_dir = project_root / config.project.state_dir
    warnings: list[str] = []
    for role in config.roles:
        checked: set[str] = set()
        for skill in role.skills:
            _validate_skill_recursive(
                warnings=warnings,
                checked=checked,
                config=config,
                project_root=project_root,
                state_dir=state_dir,
                role=role,
                skill=skill,
                dependency_of=(),
            )
    return warnings


def _validate_skill_recursive(
    *,
    warnings: list[str],
    checked: set[str],
    config: ZfConfig,
    project_root: Path,
    state_dir: Path,
    role: RoleConfig,
    skill: str,
    dependency_of: tuple[str, ...],
) -> None:
    skill = _normalize_dependency_name(skill)
    if skill in checked:
        return
    checked.add(skill)
    resolution = resolve_skill(
        project_root=project_root,
        state_dir=state_dir,
        name=skill,
        config=config,
    )
    source = resolution.path
    prefix = (
        f"role {role.instance_id!r} dependency {skill!r} "
        f"(required by {', '.join(dependency_of)!r})"
        if dependency_of
        else f"role {role.instance_id!r} skill {skill!r}"
    )
    if source is None:
        if dependency_of:
            warnings.append(
                f"role {role.instance_id!r} enables missing dependency {skill!r} "
                f"required by {', '.join(dependency_of)}"
            )
        else:
            warnings.append(
                f"role {role.instance_id!r} enables missing skill {skill!r}"
            )
        return
    collision_candidates = _format_collision_candidates(
        resolution=resolution,
        project_root=project_root,
    )
    if collision_candidates:
        warnings.append(
            f"{prefix} has multiple candidates; resolved source "
            f"{resolution.source_name!r}; candidates: {', '.join(collision_candidates)}"
        )
    metadata = read_skill_metadata(source, expected_name=skill)
    for warning in metadata.warnings:
        warnings.append(f"{prefix}: {warning}")
    for warning in _skill_routing_warnings(metadata=metadata, role=role):
        warnings.append(f"{prefix}: {warning}")
    for dependency in metadata.dependencies:
        _validate_skill_recursive(
            warnings=warnings,
            checked=checked,
            config=config,
            project_root=project_root,
            state_dir=state_dir,
            role=role,
            skill=dependency,
            dependency_of=(*dependency_of, skill),
        )


def _expanded_role_skill_requests(
    *,
    project_root: Path,
    state_dir: Path,
    role: RoleConfig,
    config: ZfConfig | None,
) -> list[tuple[str, tuple[str, ...]]]:
    """Return explicit role skills plus declared companion dependencies.

    The first tuple item is the skill name to resolve. The second is the chain
    of parent skills that made it visible. Dependencies are visible to the
    worker because an explicitly enabled skill asked for them; they do not make
    all repo skills globally available.
    """
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


def instruction_entries_for_role(
    *,
    config: ZfConfig,
    project_root: Path,
    role: RoleConfig,
) -> list[SkillLockEntry]:
    """Return role skill entries suitable for rendering in instructions."""
    return build_skill_lock_entries(
        project_root=project_root,
        state_dir=project_root / config.project.state_dir,
        role=role,
        config=config,
    )


def _sha256(path: Path) -> str:
    """Deprecation alias kept for backward compat (call sites in
    ``materialize.py`` import this name). Delegates to the canonical
    helper in ``zf.core.security.hash``; remove this alias after 1
    release once callers migrate."""
    from zf.core.security.hash import sha256_file

    return sha256_file(path)


def _format_collision_candidates(
    *,
    resolution: SkillResolution,
    project_root: Path,
) -> tuple[str, ...]:
    if len(resolution.collision_candidates) <= 1:
        return ()
    return tuple(
        f"{candidate.source_name}:{_display_path(candidate.path, project_root)}"
        for candidate in resolution.collision_candidates
    )


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve(strict=False).relative_to(
            project_root.resolve(strict=False)
        ))
    except ValueError:
        return str(path)


def _read_frontmatter(text: str) -> dict | None:
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end_idx: int | None = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None
    raw = "\n".join(lines[1:end_idx])
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _dependency_tuple(frontmatter: dict) -> tuple[str, ...]:
    raw = (
        frontmatter.get("dependencies")
        or frontmatter.get("depends_on")
        or frontmatter.get("companion_skills")
        or frontmatter.get("requires")
    )
    names = []
    seen: set[str] = set()
    for item in _string_tuple(raw):
        name = _normalize_dependency_name(item)
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return tuple(names)


def _normalize_dependency_name(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith("/SKILL.md"):
        text = str(Path(text).parent)
    if text.startswith("skills/"):
        text = text[len("skills/"):]
    if text.startswith("yoke/"):
        text = text[len("yoke/"):]
    return Path(text).name if "/" in text else text


def _skill_routing_warnings(
    *,
    metadata: SkillMetadata,
    role: RoleConfig,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if metadata.roles and not _matches(
        (role.name, role.instance_id),
        metadata.roles,
    ):
        warnings.append(
            "visibility.roles does not include role "
            f"{role.name!r}/{role.instance_id!r}"
        )
    if metadata.backends and not _matches((role.backend,), metadata.backends):
        warnings.append(
            f"visibility.backends does not include backend {role.backend!r}"
        )
    if metadata.stages and role.stages and not _matches(role.stages, metadata.stages):
        warnings.append(
            "visibility.stages does not overlap role.stages "
            f"{tuple(role.stages)!r}"
        )
    return tuple(warnings)


def _matches(values: tuple[str, ...] | list[str], allowed: tuple[str, ...]) -> bool:
    normalized_allowed = {_normalize_token(item) for item in allowed}
    if "*" in normalized_allowed:
        return True
    return any(_normalize_token(item) in normalized_allowed for item in values)


def _normalize_token(value: str) -> str:
    return str(value).strip().lower()


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list | tuple | set):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _bool_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _entry_key(entry: SkillLockEntry) -> tuple:
    return (
        entry.role,
        entry.instance_id,
        entry.task_id,
        entry.run_id,
        entry.name,
    )


def _dict_key(item: dict) -> tuple:
    return (
        item.get("role"),
        item.get("instance_id"),
        item.get("task_id"),
        item.get("run_id"),
        item.get("name"),
    )
