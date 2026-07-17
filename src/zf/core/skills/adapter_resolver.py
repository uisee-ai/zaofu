"""Project adapter skill resolver for short workflow specs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.schema import ZfConfig
from zf.core.security.hash import sha256_file
from zf.core.skills.provenance import (
    SkillCandidate,
    find_skill_candidates,
    read_skill_metadata,
)

_SUPPORTED_KINDS = frozenset({"issue", "prd", "refactor"})
_DEFAULT_POLICY_REF = Path("examples/prod/controller/common/skill-adapter-policy.yaml")


@dataclass(frozen=True)
class AdapterSkillResolverInput:
    kind: str
    project_root: Path
    project_id: str = ""
    state_dir: Path | None = None
    config: ZfConfig | None = None
    parity_scope: tuple[str, ...] = ()
    strictness: str = "standard"
    policy_path: Path | None = None


def build_project_adapter_skill_plan(
    request: AdapterSkillResolverInput,
) -> dict[str, Any]:
    """Build a deterministic project adapter skill plan.

    Skill identity and role composition are declarative: direct bundles come
    from the configured profile source, companion skills come from SKILL.md
    dependency metadata, and adapter/parity policy comes from a data file. The
    resolver only validates, resolves provenance, and composes those inputs.
    """
    kind = _normalize_kind(request.kind)
    project_root = request.project_root.expanduser().resolve()
    state_dir = _state_dir_for_request(request, project_root=project_root)
    strictness = _normalize_strictness(request.strictness)
    policy = _load_adapter_policy(request.policy_path)
    role_bundles = _load_profile_role_skill_bundles(policy, kind=kind)
    parity_scope = _normalize_parity_scope(
        kind,
        request.parity_scope,
        policy=policy,
    )
    project_key = _project_key(request.project_id)

    required_roots = _dedupe_names(
        name
        for names in role_bundles.values()
        for name in names
    )
    recommended_roots = _recommended_skill_names(policy, kind=kind)
    parity_skills: list[str] = []
    for scope in parity_scope:
        skill = _parity_skill_name(policy, scope=scope)
        if skill and skill not in recommended_roots:
            recommended_roots.append(skill)
            parity_skills.append(skill)

    loaded_by_name: dict[str, dict[str, Any]] = {}
    required, missing_required = _resolve_skill_closure(
        required_roots,
        requirement="required",
        loaded_by_name=loaded_by_name,
        project_root=project_root,
        state_dir=state_dir,
        config=request.config,
    )
    recommended, missing_recommended = _resolve_skill_closure(
        recommended_roots,
        requirement="recommended",
        loaded_by_name=loaded_by_name,
        project_root=project_root,
        state_dir=state_dir,
        config=request.config,
    )

    project_skills = _discover_project_skills(
        project_key,
        project_root=project_root,
        state_dir=state_dir,
        config=request.config,
        already_loaded=set(loaded_by_name),
    )
    for item in project_skills:
        loaded_by_name.setdefault(str(item["name"]), item)
    project_dependencies, missing_project_dependencies = _resolve_skill_closure(
        (
            dependency
            for item in project_skills
            for dependency in item.get("dependencies", [])
        ),
        requirement="project-adapter-dependency",
        loaded_by_name=loaded_by_name,
        project_root=project_root,
        state_dir=state_dir,
        config=request.config,
    )
    for name in missing_project_dependencies:
        if name not in missing_required:
            missing_required.append(name)
    required = _dedupe_names([*required, *project_dependencies])

    bundles = _role_skill_bundles(
        base_bundles=role_bundles,
        loaded_by_name=loaded_by_name,
        overlay_skill_names=[
            *parity_skills,
            *(str(item["name"]) for item in project_skills),
        ],
    )
    strict_stop_values = _strictness_stop_values(policy)
    diagnostics = _diagnostics(
        kind=kind,
        project_key=project_key,
        strictness=strictness,
        missing_required=missing_required,
        missing_recommended=missing_recommended,
        project_skills=project_skills,
        strict_stop_values=strict_stop_values,
    )
    stop = any(item["severity"] == "STOP" for item in diagnostics)
    warn = any(item["severity"] == "WARN" for item in diagnostics)
    return {
        "schema_version": "skill.adapter.plan.v2",
        "kind": kind,
        "project_id": request.project_id,
        "project_key": project_key,
        "strictness": strictness,
        "parity_scope": list(parity_scope),
        "status": "STOP" if stop else "WARN" if warn else "PASS",
        "required_skills": required,
        "recommended_skills": recommended,
        "loaded_skills": list(loaded_by_name.values()),
        "missing_required_skills": missing_required,
        "missing_recommended_skills": missing_recommended,
        # Backward-compatible aggregate used by older preflight consumers.
        "missing_skills": [*missing_required, *missing_recommended],
        "discovered_project_skills": project_skills,
        "roleSkillBundles": bundles,
        "role_skill_bundles_patch": bundles,
        "diagnostics": diagnostics,
        "policy": {
            "source_ref": str(policy["source_path"]),
            "sha256": sha256_file(policy["source_path"]),
            "fallback": (
                "generic_allowed_with_warning"
                if strictness not in strict_stop_values
                else "project_adapter_required"
            ),
            "strictness_stop_values": sorted(strict_stop_values),
        },
        "proposed_skill_backlogs": _proposed_skill_backlogs(
            kind=kind,
            project_key=project_key,
            missing_required=missing_required,
            missing_recommended=missing_recommended,
            project_skill_names=[str(item["name"]) for item in project_skills],
            strictness=strictness,
            policy=policy,
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _state_dir_for_request(request: AdapterSkillResolverInput, *, project_root: Path) -> Path:
    if request.state_dir is not None:
        state_dir = request.state_dir.expanduser()
    elif request.config is not None:
        state_dir = Path(request.config.project.state_dir).expanduser()
    else:
        state_dir = project_root / ".zf"
    if not state_dir.is_absolute():
        state_dir = project_root / state_dir
    return state_dir.resolve()


def _load_adapter_policy(policy_path: Path | None) -> dict[str, Any]:
    path = policy_path.expanduser() if policy_path is not None else _zaofu_repo_root() / _DEFAULT_POLICY_REF
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"skill adapter policy is unreadable: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"skill adapter policy YAML is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "SkillAdapterPolicy":
        raise ValueError(f"skill adapter policy must be a SkillAdapterPolicy document: {path}")
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"skill adapter policy spec must be a mapping: {path}")
    return {**spec, "source_path": path}


def _load_profile_role_skill_bundles(
    policy: dict[str, Any],
    *,
    kind: str,
) -> dict[str, list[str]]:
    ref = str(policy.get("profile_source") or "").strip()
    if not ref:
        raise ValueError("skill adapter policy spec.profile_source is required")
    policy_path = Path(policy["source_path"])
    profile_path = Path(ref).expanduser()
    if not profile_path.is_absolute():
        profile_path = policy_path.parent / profile_path
    profile_path = profile_path.resolve()
    try:
        documents = list(yaml.safe_load_all(profile_path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise ValueError(f"skill adapter profile source is unreadable: {profile_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"skill adapter profile YAML is invalid: {profile_path}: {exc}") from exc

    matches: list[dict[str, list[str]]] = []
    for document in documents:
        if not isinstance(document, dict) or document.get("kind") != "ConfigProfile":
            continue
        spec = document.get("spec")
        defaults = spec.get("flow_defaults") if isinstance(spec, dict) else None
        flow = defaults.get(kind) if isinstance(defaults, dict) else None
        raw_bundles = flow.get("roleSkillBundles") if isinstance(flow, dict) else None
        if raw_bundles is None:
            continue
        if not isinstance(raw_bundles, dict):
            raise ValueError(
                f"flow_defaults.{kind}.roleSkillBundles must be a mapping in {profile_path}"
            )
        bundles: dict[str, list[str]] = {}
        for role, raw_names in raw_bundles.items():
            if not isinstance(raw_names, list):
                raise ValueError(
                    f"roleSkillBundles.{role} must be a list in {profile_path}"
                )
            bundles[str(role)] = _dedupe_names(raw_names)
        matches.append(bundles)
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one ConfigProfile flow_defaults.{kind} in "
            f"{profile_path}, found {len(matches)}"
        )
    policy["profile_source_path"] = profile_path
    return matches[0]


def _recommended_skill_names(policy: dict[str, Any], *, kind: str) -> list[str]:
    raw = policy.get("recommended_skills")
    if not isinstance(raw, dict):
        return []
    flows = raw.get("flows")
    flow_names = flows.get(kind, []) if isinstance(flows, dict) else []
    return _dedupe_names([*(raw.get("common") or []), *(flow_names or [])])


def _parity_skill_name(policy: dict[str, Any], *, scope: str) -> str:
    parity = policy.get("parity")
    skills = parity.get("skills") if isinstance(parity, dict) else None
    return str(skills.get(scope) or "").strip() if isinstance(skills, dict) else ""


def _strictness_stop_values(policy: dict[str, Any]) -> set[str]:
    adapter = policy.get("project_adapter")
    values = adapter.get("strictness_stop_values", []) if isinstance(adapter, dict) else []
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _normalize_kind(kind: str) -> str:
    value = str(kind or "issue").strip().lower()
    return value if value in _SUPPORTED_KINDS else "issue"


def _normalize_strictness(strictness: str) -> str:
    value = str(strictness or "standard").strip().lower()
    return value or "standard"


def _normalize_parity_scope(
    kind: str,
    parity_scope: tuple[str, ...],
    *,
    policy: dict[str, Any],
) -> tuple[str, ...]:
    raw = [str(item or "").strip().lower() for item in parity_scope if str(item or "").strip()]
    if not raw:
        parity = policy.get("parity")
        defaults = parity.get("default_scopes") if isinstance(parity, dict) else None
        raw = [
            str(item).strip().lower()
            for item in (defaults.get(kind, []) if isinstance(defaults, dict) else [])
            if str(item).strip()
        ]
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _dedupe_names(names: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _project_key(project_id: str) -> str:
    value = str(project_id or "").strip().lower()
    safe = "".join(ch if ch.isalnum() else "-" for ch in value).strip("-")
    return safe


def _resolve_payload(
    name: str,
    *,
    project_root: Path,
    state_dir: Path,
    config: ZfConfig | None,
) -> dict[str, Any] | None:
    candidates = list(find_skill_candidates(
        project_root=project_root,
        state_dir=state_dir,
        name=name,
        config=config,
    ))
    candidates.extend(_canonical_zaofu_candidates(name, seen=candidates))
    if not candidates:
        return None
    selected = candidates[0]
    return _skill_payload(name=name, candidate=selected, project_root=project_root)


def _resolve_skill_closure(
    roots: Any,
    *,
    requirement: str,
    loaded_by_name: dict[str, dict[str, Any]],
    project_root: Path,
    state_dir: Path,
    config: ZfConfig | None,
) -> tuple[list[str], list[str]]:
    """Resolve roots and their declared dependencies without semantic names."""

    queue = _dedupe_names(roots)
    closure: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        closure.append(name)
        existing = loaded_by_name.get(name)
        if existing is not None:
            queue.extend(_dedupe_names(existing.get("dependencies", [])))
            continue
        resolved = _resolve_payload(
            name,
            project_root=project_root,
            state_dir=state_dir,
            config=config,
        )
        if resolved is None:
            missing.append(name)
            continue
        loaded_by_name[name] = {**resolved, "requirement": requirement}
        queue.extend(_dedupe_names(resolved.get("dependencies", [])))
    return closure, missing


def _canonical_zaofu_candidates(
    name: str,
    *,
    seen: list[SkillCandidate],
) -> list[SkillCandidate]:
    root = _zaofu_repo_root()
    path = root / "skills" / name / "SKILL.md"
    if not path.is_file():
        return []
    resolved = path.resolve(strict=False)
    for candidate in seen:
        if candidate.path.resolve(strict=False) == resolved:
            return []
    return [SkillCandidate(source_name="zaofu", path=path)]


def _zaofu_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _skill_payload(
    *,
    name: str,
    candidate: SkillCandidate,
    project_root: Path,
) -> dict[str, Any]:
    path = candidate.path
    metadata = read_skill_metadata(path, expected_name=name)
    return {
        "name": name,
        "path": str(path.parent),
        "source_ref": str(path),
        "source_name": candidate.source_name,
        "sha256": sha256_file(path),
        "description": metadata.description,
        "stages": list(metadata.stages),
        "roles": list(metadata.roles),
        "tags": list(metadata.tags),
        "dependencies": list(metadata.dependencies),
        "auto_inject": metadata.auto_inject,
        "load_on_demand": metadata.load_on_demand,
        "warnings": list(metadata.warnings),
        "owner": _skill_owner(candidate.source_name),
        "display_path": _display_path(path, project_root),
    }


def _skill_owner(source_name: str) -> str:
    if source_name == "project":
        return "project"
    if source_name == "state":
        return "runtime-state"
    if source_name == "zaofu":
        return "zaofu"
    return f"skill-source:{source_name}"


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _discover_project_skills(
    project_key: str,
    *,
    project_root: Path,
    state_dir: Path,
    config: ZfConfig | None,
    already_loaded: set[str],
) -> list[dict[str, Any]]:
    if not project_key:
        return []
    names: set[str] = set()
    for root in _skill_search_roots(
        project_root=project_root,
        state_dir=state_dir,
        config=config,
        include_canonical=False,
    ):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or not (child / "SKILL.md").is_file():
                continue
            name = child.name
            if not name.startswith(f"{project_key}-") or name in already_loaded:
                continue
            names.add(name)
    items: list[dict[str, Any]] = []
    for name in sorted(names):
        resolved = _resolve_payload(
            name,
            project_root=project_root,
            state_dir=state_dir,
            config=config,
        )
        if resolved is not None:
            items.append({**resolved, "requirement": "project-adapter"})
    return items


def _skill_search_roots(
    *,
    project_root: Path,
    state_dir: Path,
    config: ZfConfig | None,
    include_canonical: bool = True,
) -> list[Path]:
    roots = [
        state_dir / "skills",
        project_root / "skills",
    ]
    if include_canonical:
        roots.append(_zaofu_repo_root() / "skills")
    if config is not None:
        for source in config.skill_sources:
            root = Path(source.path).expanduser()
            if not root.is_absolute():
                root = project_root / root
            roots.append(root)
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        resolved = root.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(root)
    return out


def _role_skill_bundles(
    *,
    base_bundles: dict[str, list[str]],
    loaded_by_name: dict[str, dict[str, Any]],
    overlay_skill_names: list[str],
) -> dict[str, list[str]]:
    loaded_names = set(loaded_by_name)
    bundles = {
        role: [name for name in names if name in loaded_names]
        for role, names in base_bundles.items()
    }
    for name in _dedupe_names(overlay_skill_names):
        payload = loaded_by_name.get(name)
        if payload is None:
            continue
        for role in _overlay_roles(payload, available_roles=set(bundles)):
            if name not in bundles[role]:
                bundles[role].append(name)
    return {key: value for key, value in bundles.items() if value}


def _overlay_roles(
    payload: dict[str, Any],
    *,
    available_roles: set[str],
) -> list[str]:
    """Route an overlay from metadata, never from its skill name."""

    explicit = {
        str(role).strip()
        for role in payload.get("roles", [])
        if str(role).strip() in available_roles
    }
    stages = {
        str(stage).strip().lower().replace("_", "-")
        for stage in payload.get("stages", [])
        if str(stage).strip()
    }
    matched = set(explicit)
    for role in available_roles:
        role_tokens = set(role.lower().replace("_", "-").split("-"))
        if any(_stage_matches_role(stage, role_tokens=role_tokens) for stage in stages):
            matched.add(role)
    return sorted(matched)


def _stage_matches_role(stage: str, *, role_tokens: set[str]) -> bool:
    if stage in role_tokens:
        return True
    if stage in {"impl", "fix"}:
        return bool(role_tokens & {"impl", "fix", "dev"})
    if stage in {"verify", "verification", "test", "discovery"}:
        return bool(
            role_tokens
            & {"verify", "verification", "test", "discovery", "parity"}
        )
    if stage in {"plan", "planner", "replan", "triage"}:
        return bool(role_tokens & {"plan", "planner", "synth", "triage"})
    return False


def _diagnostics(
    *,
    kind: str,
    project_key: str,
    strictness: str,
    missing_required: list[str],
    missing_recommended: list[str],
    project_skills: list[dict[str, Any]],
    strict_stop_values: set[str],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if missing_required:
        diagnostics.append({
            "severity": "STOP",
            "kind": "adapter_required_skill_missing",
            "title": "必需 workflow skill 缺失",
            "message": ", ".join(missing_required),
            "fix_it": "配置 skill_sources 或补齐对应 skills 后重新生成 workflow intake/draft。",
            "safe_auto_fix": False,
        })
    if missing_recommended:
        diagnostics.append({
            "severity": "WARN",
            "kind": "adapter_recommended_skill_missing",
            "title": "推荐 workflow skill 缺失",
            "message": ", ".join(missing_recommended),
            "fix_it": "按需补充推荐 skills；standard 模式可继续，full-parity/release 需人工确认。",
            "safe_auto_fix": True,
        })
    if kind == "refactor" and project_key:
        project_skill_names = [str(item["name"]) for item in project_skills]
        has_project = bool(project_skills)
        has_parity = any(
            (
                {str(stage).strip().lower() for stage in item.get("stages", [])}
                & {"verify", "verification", "test", "judge"}
            )
            or "parity" in {
                str(tag).strip().lower() for tag in item.get("tags", [])
            }
            for item in project_skills
        )
        if not has_project or not has_parity:
            diagnostics.append({
                "severity": "STOP" if strictness in strict_stop_values else "WARN",
                "kind": "project_adapter_skill_missing",
                "title": "项目 adapter/parity skill 覆盖不足",
                "message": (
                    f"project={project_key}; discovered="
                    f"{', '.join(project_skill_names) if project_skill_names else 'none'}"
                ),
                "fix_it": (
                    f"生成 {project_key}-scan-inventory / "
                    f"{project_key}-parity-contract / "
                    f"{project_key}-verify-rescan 等项目专属 skills,"
                    "或在 proposal 中降级 strictness。"
                ),
                "safe_auto_fix": True,
            })
    return diagnostics


def _proposed_skill_backlogs(
    *,
    kind: str,
    project_key: str,
    missing_required: list[str],
    missing_recommended: list[str],
    project_skill_names: list[str],
    strictness: str,
    policy: dict[str, Any],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for name in [*missing_required, *missing_recommended]:
        items.append({
            "skill": name,
            "status": "proposed",
            "reason": "missing required/recommended workflow skill",
        })
    if kind == "refactor" and project_key and not project_skill_names:
        adapter = policy.get("project_adapter")
        suffixes_by_kind = (
            adapter.get("backlog_suffixes") if isinstance(adapter, dict) else None
        )
        suffixes = (
            suffixes_by_kind.get(kind, [])
            if isinstance(suffixes_by_kind, dict)
            else []
        )
        for suffix in _dedupe_names(suffixes):
            items.append({
                "skill": f"{project_key}-{suffix}",
                "status": "proposed",
                "reason": f"strictness={strictness} needs project adapter evidence",
            })
    return items
