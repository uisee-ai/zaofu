"""Project adapter skill resolver for short workflow specs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.security.hash import sha256_file
from zf.core.skills.provenance import (
    SkillCandidate,
    find_skill_candidates,
    read_skill_metadata,
)


_COMMON_REQUIRED = (
    "zf-plan-task-map-contract",
    "zf-goal-closure-replan-contract",
    "zf-gap-task-synth",
    "zf-harness-done-contract",
    "zf-mechanical-claim-verifier",
    # yoke 角色边界 wrapper(方法论技能经 frontmatter dependencies 闭包
    # 物化,不在此列名):planner / writer / 验收读者 / 终审门。
    "zf-yoke-planner-role-context",
    "zf-yoke-dev-worker-role-context",
    "zf-yoke-test-evaluator-role-context",
    "zf-yoke-quality-gate-role-context",
)
_COMMON_RECOMMENDED = (
    "zf-dynamic-artifact-gate",
    "zf-workflow-adaptation-boundary",
    "zf-project-adapter-matrix-enrichment",
    "zf-verify-gap-producer-contract",
)
_FLOW_REQUIRED = {
    "issue": ("zf-issue-plan-synth",),
    "prd": ("zf-prd-plan-synth",),
    "refactor": ("zf-refactor-plan-synth", "zf-verify-rescan-replan"),
}
_FLOW_RECOMMENDED = {
    "issue": (),
    "prd": (),
    "refactor": (
        "zf-refactor-generalization-audit",
        "zf-research-preflight-law",
    ),
}
_PARITY_SCOPE_SKILLS = {
    "provider": "zf-provider-contract-parity",
    "tools": "zf-tool-skill-parity",
    "skills": "zf-tool-skill-parity",
    "webui": "zf-webui-tui-parity",
    "tui": "zf-webui-tui-parity",
    "memory": "zf-memory-context-parity",
    "context": "zf-memory-context-parity",
}
_DEFAULT_REFACTOR_PARITY_SCOPE = (
    "core",
    "cli",
    "api",
    "web",
    "runtime",
)
_STRICT_STOP_VALUES = {"full-parity", "release", "strict"}


@dataclass(frozen=True)
class AdapterSkillResolverInput:
    kind: str
    project_root: Path
    project_id: str = ""
    state_dir: Path | None = None
    config: ZfConfig | None = None
    parity_scope: tuple[str, ...] = ()
    strictness: str = "standard"


def build_project_adapter_skill_plan(
    request: AdapterSkillResolverInput,
) -> dict[str, Any]:
    """Build a deterministic project adapter skill plan.

    The resolver owns provenance and deterministic policy only. It does not
    invent project semantics; project-specific guidance remains in skills that
    are discovered by name prefix or supplied through configured skill_sources.
    """
    kind = _normalize_kind(request.kind)
    project_root = request.project_root.expanduser().resolve()
    state_dir = _state_dir_for_request(request, project_root=project_root)
    strictness = _normalize_strictness(request.strictness)
    parity_scope = _normalize_parity_scope(kind, request.parity_scope)
    project_key = _project_key(request.project_id)

    required = [*_COMMON_REQUIRED, *_FLOW_REQUIRED.get(kind, ())]
    recommended = [*_COMMON_RECOMMENDED, *_FLOW_RECOMMENDED.get(kind, ())]
    for scope in parity_scope:
        skill = _PARITY_SCOPE_SKILLS.get(scope)
        if skill and skill not in recommended:
            recommended.append(skill)

    loaded: list[dict[str, Any]] = []
    missing_required: list[str] = []
    missing_recommended: list[str] = []
    resolved_names: set[str] = set()
    for name in required:
        resolved = _resolve_payload(
            name,
            project_root=project_root,
            state_dir=state_dir,
            config=request.config,
        )
        if resolved is None:
            missing_required.append(name)
            continue
        resolved_names.add(name)
        loaded.append({**resolved, "requirement": "required"})
    for name in recommended:
        if name in resolved_names:
            continue
        resolved = _resolve_payload(
            name,
            project_root=project_root,
            state_dir=state_dir,
            config=request.config,
        )
        if resolved is None:
            missing_recommended.append(name)
            continue
        resolved_names.add(name)
        loaded.append({**resolved, "requirement": "recommended"})

    project_skills = _discover_project_skills(
        project_key,
        project_root=project_root,
        state_dir=state_dir,
        config=request.config,
        already_loaded=resolved_names,
    )
    for item in project_skills:
        resolved_names.add(str(item["name"]))
    loaded.extend(project_skills)

    bundles = _role_skill_bundles(
        kind=kind,
        loaded_names={str(item["name"]) for item in loaded},
        project_skill_names=[str(item["name"]) for item in project_skills],
        parity_scope=parity_scope,
    )
    diagnostics = _diagnostics(
        kind=kind,
        project_key=project_key,
        strictness=strictness,
        missing_required=missing_required,
        missing_recommended=missing_recommended,
        project_skill_names=[str(item["name"]) for item in project_skills],
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
        "loaded_skills": loaded,
        "missing_required_skills": missing_required,
        "missing_recommended_skills": missing_recommended,
        # Backward-compatible aggregate used by older preflight consumers.
        "missing_skills": [*missing_required, *missing_recommended],
        "discovered_project_skills": project_skills,
        "roleSkillBundles": bundles,
        "role_skill_bundles_patch": bundles,
        "diagnostics": diagnostics,
        "policy": {
            "fallback": (
                "generic_allowed_with_warning"
                if strictness not in _STRICT_STOP_VALUES
                else "project_adapter_required"
            ),
            "strictness_stop_values": sorted(_STRICT_STOP_VALUES),
        },
        "proposed_skill_backlogs": _proposed_skill_backlogs(
            kind=kind,
            project_key=project_key,
            missing_required=missing_required,
            missing_recommended=missing_recommended,
            project_skill_names=[str(item["name"]) for item in project_skills],
            strictness=strictness,
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


def _normalize_kind(kind: str) -> str:
    value = str(kind or "issue").strip().lower()
    return value if value in {"issue", "prd", "refactor"} else "issue"


def _normalize_strictness(strictness: str) -> str:
    value = str(strictness or "standard").strip().lower()
    return value or "standard"


def _normalize_parity_scope(kind: str, parity_scope: tuple[str, ...]) -> tuple[str, ...]:
    raw = [str(item or "").strip().lower() for item in parity_scope if str(item or "").strip()]
    if not raw and kind == "refactor":
        raw = list(_DEFAULT_REFACTOR_PARITY_SCOPE)
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


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
    kind: str,
    loaded_names: set[str],
    project_skill_names: list[str],
    parity_scope: tuple[str, ...],
) -> dict[str, list[str]]:
    bundles: dict[str, list[str]] = {}
    if kind == "issue":
        bundles["issue-triage"] = _present([
            "zf-workflow-adaptation-boundary",
            "zf-project-adapter-matrix-enrichment",
            "zf-issue-plan-synth",
            "zf-plan-task-map-contract",
            "zf-gap-task-synth",
            "zf-yoke-planner-role-context",
        ], loaded_names)
        bundles["fix"] = _present([
            "zf-harness-done-contract",
            "zf-yoke-dev-worker-role-context",
        ], loaded_names)
        bundles["verify"] = _present([
            "zf-workflow-adaptation-boundary",
            "zf-project-adapter-matrix-enrichment",
            "zf-verify-gap-producer-contract",
            "zf-mechanical-claim-verifier",
            "zf-goal-closure-replan-contract",
            "zf-yoke-test-evaluator-role-context",
        ], loaded_names)
        bundles["judge-issue"] = _present([
            "zf-goal-closure-replan-contract",
            "zf-yoke-quality-gate-role-context",
        ], loaded_names)
        return {key: value for key, value in bundles.items() if value}
    if kind == "prd":
        bundles["scan"] = _present([
            "zf-workflow-adaptation-boundary",
            "zf-project-adapter-matrix-enrichment",
            "zf-prd-plan-synth",
            "zf-plan-task-map-contract",
        ], loaded_names)
        bundles["planner"] = _present([
            "zf-workflow-adaptation-boundary",
            "zf-project-adapter-matrix-enrichment",
            "zf-prd-plan-synth",
            "zf-plan-task-map-contract",
            "zf-gap-task-synth",
            "zf-yoke-planner-role-context",
        ], loaded_names)
        bundles["impl"] = _present([
            "zf-harness-done-contract",
            "zf-yoke-dev-worker-role-context",
        ], loaded_names)
        bundles["verify"] = _present([
            "zf-workflow-adaptation-boundary",
            "zf-project-adapter-matrix-enrichment",
            "zf-verify-gap-producer-contract",
            "zf-mechanical-claim-verifier",
            "zf-goal-closure-replan-contract",
            "zf-yoke-test-evaluator-role-context",
        ], loaded_names)
        bundles["judge-prd"] = _present([
            "zf-goal-closure-replan-contract",
            "zf-yoke-quality-gate-role-context",
        ], loaded_names)
        return {key: value for key, value in bundles.items() if value}

    cross_parity = _present([
        _PARITY_SCOPE_SKILLS[scope]
        for scope in parity_scope
        if scope in _PARITY_SCOPE_SKILLS
    ], loaded_names)
    project_scan = _filter_project_skills(project_skill_names, ("scan", "inventory", "target"))
    project_plan = _filter_project_skills(project_skill_names, ("plan", "gap", "synth"))
    project_impl = _filter_project_skills(project_skill_names, ("impl", "handoff", "assembly"))
    project_verify = _filter_project_skills(
        project_skill_names,
        ("verify", "verifier", "parity", "e2e", "dashboard", "web", "tui"),
    )
    bundles["scan-contract"] = _present([
        "zf-workflow-adaptation-boundary",
        "zf-project-adapter-matrix-enrichment",
        "zf-plan-task-map-contract",
        "zf-refactor-plan-synth",
        *project_scan,
    ], loaded_names)
    bundles["refactor-plan-synth"] = _present([
        "zf-workflow-adaptation-boundary",
        "zf-project-adapter-matrix-enrichment",
        "zf-refactor-plan-synth",
        "zf-plan-task-map-contract",
        "zf-gap-task-synth",
        "zf-yoke-planner-role-context",
        *project_plan,
    ], loaded_names)
    bundles["impl"] = _present([
        "zf-harness-done-contract",
        "zf-yoke-dev-worker-role-context",
        *cross_parity,
        *project_impl,
    ], loaded_names)
    bundles["verify"] = _present([
        "zf-workflow-adaptation-boundary",
        "zf-project-adapter-matrix-enrichment",
        "zf-verify-gap-producer-contract",
        "zf-mechanical-claim-verifier",
        "zf-goal-closure-replan-contract",
        "zf-verify-rescan-replan",
        "zf-yoke-test-evaluator-role-context",
        *cross_parity,
        *project_verify,
    ], loaded_names)
    bundles["refactor-verify-bridge"] = _present([
        "zf-verify-gap-producer-contract",
        "zf-verify-rescan-replan",
        "zf-gap-task-synth",
        *project_verify,
    ], loaded_names)
    bundles["module-parity-scan"] = _present([
        "zf-verify-rescan-replan",
        *cross_parity,
        *project_verify,
    ], loaded_names)
    bundles["judge-refactor"] = _present([
        "zf-workflow-adaptation-boundary",
        "zf-project-adapter-matrix-enrichment",
        "zf-verify-gap-producer-contract",
        "zf-goal-closure-replan-contract",
        "zf-mechanical-claim-verifier",
        "zf-yoke-quality-gate-role-context",
        *project_verify,
    ], loaded_names)
    return {key: value for key, value in bundles.items() if value}


def _present(names: list[str], loaded_names: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in loaded_names or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _filter_project_skills(names: list[str], terms: tuple[str, ...]) -> list[str]:
    return [
        name for name in names
        if any(term in name.lower() for term in terms)
    ]


def _diagnostics(
    *,
    kind: str,
    project_key: str,
    strictness: str,
    missing_required: list[str],
    missing_recommended: list[str],
    project_skill_names: list[str],
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
        has_project = bool(project_skill_names)
        has_parity = any(
            term in name.lower()
            for name in project_skill_names
            for term in ("parity", "verify", "verifier", "e2e")
        )
        if not has_project or not has_parity:
            diagnostics.append({
                "severity": "STOP" if strictness in _STRICT_STOP_VALUES else "WARN",
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
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for name in [*missing_required, *missing_recommended]:
        items.append({
            "skill": name,
            "status": "proposed",
            "reason": "missing required/recommended workflow skill",
        })
    if kind == "refactor" and project_key and not project_skill_names:
        for suffix in ("scan-inventory", "parity-contract", "verify-rescan"):
            items.append({
                "skill": f"{project_key}-{suffix}",
                "status": "proposed",
                "reason": f"strictness={strictness} needs project adapter evidence",
            })
    return items
