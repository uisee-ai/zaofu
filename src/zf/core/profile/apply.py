"""Apply a profile/recommendation onto project files (doc 102 §4.3 wire-up).

Three sinks: materialize a fresh zf.yaml from the chosen archetype, fill
``quality_gates.static.required_checks`` no-clobber (PB5), and inject a managed
"detected stack" section into AGENTS.md non-destructively (PB6). YAML round-trip
is acceptable for the materialize path (file is freshly generated); the
no-clobber path only ever *adds* when the list is empty.
"""

from __future__ import annotations

import copy
import glob
import shutil
from pathlib import Path

import yaml

from zf.core.config.loader import load_config
from zf.core.config.presets import get_preset
from zf.core.profile.schema import ProjectProfile, Recommendation

PROFILE_BLOCK_START = "<!-- ZF:PROFILE:START -->"
PROFILE_BLOCK_END = "<!-- ZF:PROFILE:END -->"
NOTES_BLOCK_START = "<!-- ZF:NOTES:START -->"
NOTES_BLOCK_END = "<!-- ZF:NOTES:END -->"


def apply_project_notes(claude_md_path: str | Path, text: str, *, write: bool = False) -> dict:
    """Write operator free-text notes into CLAUDE.md (doc 102 §5, human instead of LLM).

    Lands in a managed ``ZF:NOTES`` block so it is idempotent and non-destructive
    to the rest of CLAUDE.md. Empty text is a no-op.
    """
    text = (text or "").strip()
    if not text:
        return {"action": "noop"}
    path = Path(claude_md_path)
    section = f"{NOTES_BLOCK_START}\n## 项目说明 (operator notes)\n\n{text}\n{NOTES_BLOCK_END}"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if NOTES_BLOCK_START in original and NOTES_BLOCK_END in original:
        pre = original.split(NOTES_BLOCK_START, 1)[0].rstrip()
        post = original.split(NOTES_BLOCK_END, 1)[1].lstrip()
        updated = f"{pre}\n\n{section}\n\n{post}".rstrip() + "\n"
        action = "updated"
    else:
        sep = "\n\n" if original.strip() else ""
        updated = f"{original.rstrip()}{sep}{section}\n"
        action = "created"
    if write:
        path.write_text(updated, encoding="utf-8")
    return {"action": action}


def materialize_zf_yaml(
    archetype: str, project_name: str, recommendation: Recommendation
) -> str:
    """Materialize a zf.yaml from the recommended archetype.

    A validated prod *flow* is copied verbatim (env-parameterised, no templating);
    a lightweight *preset* is generated and gets the stack overlay injected.
    """
    from zf.core.profile.flows import is_flow_id, read_flow_yaml

    if is_flow_id(archetype):
        text = read_flow_yaml(archetype)
        if text is not None:
            return _flow_yaml_with_required_checks(
                text, recommendation.required_checks,
            )
        archetype = "minimal"  # flow yaml unavailable → safe fallback
    preset = copy.deepcopy(get_preset(archetype))
    qg = preset.setdefault("quality_gates", {}).setdefault("static", {})
    if not qg.get("required_checks"):
        qg["required_checks"] = list(recommendation.required_checks)
    if recommendation.harness_profile and recommendation.harness_profile != "baseline":
        preset.setdefault("workflow", {})["harness_profile"] = recommendation.harness_profile
    text = yaml.dump(preset, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return text.replace("{project_name}", project_name)


def _flow_yaml_with_required_checks(text: str, checks) -> str:
    """Inject detected static checks into a freshly materialized flow YAML.

    Prod controller examples intentionally keep multi-lane gates as operator
    TODOs.  Bootstrap, however, already has a deterministic project profile; if
    that profile yields gate commands, the generated project YAML should be
    runnable without immediately failing the combined-candidate gate.
    """
    proposed = [str(check).strip() for check in checks or [] if str(check).strip()]
    if not proposed:
        return text
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return text
    changed = False
    for doc in docs:
        if not isinstance(doc, dict) or str(doc.get("kind") or "") != "ZfConfig":
            continue
        spec = doc.setdefault("spec", {})
        if not isinstance(spec, dict):
            continue
        qg = spec.setdefault("quality_gates", {}).setdefault("static", {})
        if not isinstance(qg, dict):
            continue
        existing = [
            str(check).strip()
            for check in qg.get("required_checks") or []
            if str(check).strip()
        ]
        if existing:
            continue
        qg["required_checks"] = list(proposed)
        qg.setdefault(
            "on_fail",
            "combined candidate tree failed static gate — 修复后重触发集成",
        )
        changed = True
    if not changed:
        return text
    return yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True)


def materialize_flow_assets(
    archetype: str,
    target_root: str | Path,
    *,
    config_path: str | Path | None = None,
) -> dict:
    """Copy source assets required by a materialized prod flow.

    Short controller YAML may reference sibling profile files and repo-local
    skills.  A bootstrap target must not be left with dangling
    ``profile_sources`` or skill source paths, so this copies the referenced
    profile files and the enabled skill directories into the project.
    """
    from zf.core.profile.flows import flow_path

    source_path = flow_path(archetype)
    if source_path is None:
        return {"profile_sources": [], "skills": [], "rewrote_skill_sources": False}
    root = Path(target_root).resolve()
    cfg_path = Path(config_path or root / "zf.yaml").resolve()
    copied_profiles = _copy_flow_profile_sources(source_path, cfg_path, root)
    rewrote = _rewrite_flow_skill_sources_to_local(cfg_path)
    copied_skills = _copy_flow_skills(source_path, cfg_path, root)
    # fb9aa16a 起 profile 自带绝对 skill_sources;vendor 完启用技能后把
    # 拷贝件里的源统一重写为项目本地 `skills`,bootstrap 目标保持自包含。
    rewrote_profiles = _rewrite_copied_profile_skill_sources(
        root, copied_profiles,
    )
    return {
        "profile_sources": copied_profiles,
        "skills": copied_skills,
        "rewrote_skill_sources": rewrote or rewrote_profiles,
    }


def materialize_config_skills(
    config_path: str | Path,
    target_root: str | Path,
) -> dict:
    """Vendor the enabled skill closure for a dynamically generated config.

    Flow drafts are commonly created from a short-lived Git worktree. Keeping
    that checkout in ``skill_sources`` makes the generated project impossible
    to resume after worktree cleanup, so project init owns this materialization
    boundary just like profile bootstrap does.
    """

    cfg_path = Path(config_path).resolve()
    root = Path(target_root).resolve()
    config = load_config(cfg_path)
    required = {
        str(skill)
        for role in config.roles
        for skill in list(getattr(role, "skills", []) or [])
        if str(skill).strip()
    }
    copied = _copy_flow_skills(cfg_path, cfg_path, root)
    copied_names = {Path(path).name for path in copied}
    missing = sorted(required - copied_names)
    if missing:
        raise FileNotFoundError(
            "could not materialize enabled project skill(s): "
            + ", ".join(missing)
        )
    rewrote = False
    if copied:
        rewrote = _rewrite_config_skill_sources_to_local(cfg_path)
    return {
        "skills": copied,
        "rewrote_skill_sources": rewrote,
    }


def _copy_flow_profile_sources(
    source_path: Path,
    config_path: Path,
    target_root: Path,
) -> list[str]:
    refs = _profile_source_refs_from_config(config_path)
    copied: list[str] = []
    for ref in refs:
        pattern = ref if Path(ref).is_absolute() else str(source_path.parent / ref)
        for match in sorted(Path(p).resolve() for p in glob.glob(pattern)):
            if not match.is_file():
                continue
            rel = _relative_profile_target(ref, match, source_path.parent)
            dest = target_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(match, dest)
            copied.append(str(dest.relative_to(target_root)))
    return sorted(dict.fromkeys(copied))


def _profile_source_refs_from_config(config_path: Path) -> list[str]:
    refs: list[str] = []
    try:
        docs = list(yaml.safe_load_all(config_path.read_text(encoding="utf-8")))
    except yaml.YAMLError:
        return refs
    for doc in docs:
        if not isinstance(doc, dict) or str(doc.get("kind") or "") != "ZfConfig":
            continue
        spec = doc.get("spec") or {}
        if not isinstance(spec, dict):
            continue
        raw = spec.get("profile_sources") or []
        raw_items = raw if isinstance(raw, list) else [raw]
        for item in raw_items:
            if isinstance(item, str) and item.strip():
                refs.append(item.strip())
            elif isinstance(item, dict) and str(item.get("path") or "").strip():
                refs.append(str(item["path"]).strip())
    return refs


def _relative_profile_target(ref: str, match: Path, source_base: Path) -> Path:
    if any(ch in ref for ch in "*?["):
        try:
            return match.relative_to(source_base)
        except ValueError:
            return Path(match.name)
    return Path(ref)


def _rewrite_flow_skill_sources_to_local(config_path: Path) -> bool:
    try:
        docs = list(yaml.safe_load_all(config_path.read_text(encoding="utf-8")))
    except yaml.YAMLError:
        return False
    changed = False
    for doc in docs:
        if not isinstance(doc, dict) or str(doc.get("kind") or "") != "ZfConfig":
            continue
        spec = doc.get("spec") or {}
        if not isinstance(spec, dict):
            continue
        sources = spec.get("skill_sources") or []
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw_path = str(source.get("path") or "")
            if raw_path in {"../../../skills", "../../skills"}:
                source["path"] = "skills"
                changed = True
    if changed:
        config_path.write_text(
            yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return changed


def _rewrite_config_skill_sources_to_local(config_path: Path) -> bool:
    try:
        docs = list(yaml.safe_load_all(config_path.read_text(encoding="utf-8")))
    except yaml.YAMLError:
        return False
    changed = False
    for doc in docs:
        if not isinstance(doc, dict) or str(doc.get("kind") or "") not in {
            "ZfConfig",
            "ConfigProfile",
        }:
            continue
        spec = doc.get("spec") or {}
        sources = spec.get("skill_sources") if isinstance(spec, dict) else None
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw_path = str(source.get("path") or "").strip()
            if raw_path and raw_path != "skills":
                source["path"] = "skills"
                changed = True
    if changed:
        config_path.write_text(
            yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return changed


def _copy_flow_skills(source_path: Path, config_path: Path, target_root: Path) -> list[str]:
    try:
        config = load_config(config_path)
    except Exception:
        return []
    skill_names = sorted({
        str(skill)
        for role in config.roles
        for skill in list(getattr(role, "skills", []) or [])
        if str(skill).strip()
    })
    if not skill_names:
        return []
    source_roots = _source_skill_roots(source_path)
    # profile 声明的 skill_sources(fb9aa16a 起为绝对路径)也是 vendor
    # 来源——启用技能一并拷进项目本地,拷贝件的源随后统一重写为 `skills`。
    for source in list(getattr(config, "skill_sources", []) or []):
        raw = str(getattr(source, "path", "") or "").strip()
        if not raw:
            continue
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = config_path.parent / root
        if root.is_dir():
            source_roots.append(root.resolve())
    # bundle 直挂的裸 yoke 方法论技能(2026-07-08 agent-skills 退役后)
    # 从各 skills 根的兄弟 yoke/ 目录 vendor,保持 bootstrap 自包含契约。
    for root in list(source_roots):
        yoke_root = root.parent / "yoke"
        if yoke_root.is_dir():
            source_roots.append(yoke_root.resolve())
    from zf.core.skills.provenance import read_skill_metadata

    copied: list[str] = []
    target_skills = target_root / "skills"
    # Vendor the transitive dependency closure, not just directly-enabled
    # skills: a `zf-yoke-*-role-context` wrapper declares `dependencies:`
    # (tdd-evidence, source-verification, ...) that are materialized on demand
    # at runtime. If bootstrap copies only the wrapper, those method skills
    # resolve at runtime via the zaofu repo `yoke/` — the project is NOT
    # self-contained and breaks on another host or a stale repo checkout
    # (2026-07-08 E2E finding). Closure-vendor so the project carries every
    # skill it can enable.
    queue: list[str] = list(skill_names)
    seen: set[str] = set()
    while queue:
        skill = queue.pop(0)
        if skill in seen:
            continue
        seen.add(skill)
        source_dir = next(
            (root / skill for root in source_roots if (root / skill / "SKILL.md").is_file()),
            None,
        )
        if source_dir is None:
            continue
        dest = target_skills / skill
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir, dest)
        copied.append(str(dest.relative_to(target_root)))
        try:
            meta = read_skill_metadata(source_dir / "SKILL.md", expected_name=skill)
        except Exception:
            continue
        for dep in meta.dependencies:
            dep_name = str(dep).strip()
            if dep_name and dep_name not in seen:
                queue.append(dep_name)
    return copied


def _source_skill_roots(source_path: Path) -> list[Path]:
    roots = [source_path.parent / "../../../skills", source_path.parent / "../../skills"]
    return [root.resolve() for root in roots if root.exists()]


def _rewrite_copied_profile_skill_sources(
    target_root: Path,
    copied_profiles: list[str],
) -> bool:
    """拷贝进项目的 ConfigProfile 里,skill_sources 的机器绝对路径重写为
    项目本地 `skills`。启用技能已由 `_copy_flow_skills` vendor;留绝对
    路径 = bootstrap 目标在别的机器上悬挂 + strict 模式必炸。"""
    changed = False
    for rel in copied_profiles:
        path = target_root / rel
        try:
            docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError):
            continue
        doc_changed = False
        for doc in docs:
            if not isinstance(doc, dict) or str(doc.get("kind") or "") != "ConfigProfile":
                continue
            spec = doc.get("spec") or {}
            sources = spec.get("skill_sources") if isinstance(spec, dict) else None
            if not isinstance(sources, list):
                continue
            for source in sources:
                if (
                    isinstance(source, dict)
                    and str(source.get("path") or "").strip() not in ("", "skills")
                ):
                    source["path"] = "skills"
                    doc_changed = True
        if doc_changed:
            path.write_text(
                yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            changed = True
    return changed


def fill_required_checks(config_path: str | Path, checks, *, write: bool = False) -> dict:
    """No-clobber fill of quality_gates.static.required_checks (PB5).

    Returns {"action": "filled"|"kept"|"noop"|"skipped", "existing": [...], "proposed": [...]}.
    Only writes when the current list is empty and ``write`` is True. A prod-flow
    config is multi-document (kind envelope) and owns its own gates — it is
    skipped (and never crashes the single-doc loader).
    """
    path = Path(config_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"action": "skipped", "reason": "multi-doc flow config", "proposed": list(checks)}
    if not isinstance(raw, dict):
        return {"action": "skipped", "reason": "non-mapping config", "proposed": list(checks)}
    qg = raw.setdefault("quality_gates", {}).setdefault("static", {})
    existing = list(qg.get("required_checks") or [])
    proposed = list(checks)
    if existing:
        return {"action": "kept", "existing": existing, "proposed": proposed}
    if not proposed:
        return {"action": "noop", "existing": existing, "proposed": proposed}
    if write:
        qg["required_checks"] = proposed
        path.write_text(
            yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return {"action": "filled", "existing": existing, "proposed": proposed}


def scaffold_from_zero(root: str | Path, profile: ProjectProfile, *, write: bool = False) -> dict:
    """Create a minimal src/tests/README scaffold so cold-start doesn't penalize
    a from-0 project (doc 102 §1.3.3). Non-destructive: only creates what's missing.
    """
    base = Path(root)
    created: list[str] = []
    for rel in ("src", "tests"):
        for unit in profile.units:
            unit_base = base if unit.root == "." else base / unit.root
            target = unit_base / rel
            key = str(target.relative_to(base)) if target.is_relative_to(base) else str(target)
            if not target.exists() and key not in created:
                created.append(key)
                if write:
                    target.mkdir(parents=True, exist_ok=True)
                    (target / ".gitkeep").write_text("", encoding="utf-8")
    readme = base / "README.md"
    if not readme.exists():
        created.append("README.md")
        if write:
            langs = "+".join(profile.languages) or "project"
            readme.write_text(f"# {base.name}\n\n{langs} project scaffolded by `zf profile bootstrap`.\n",
                              encoding="utf-8")
    return {"created": created}


def render_stack_section(profile: ProjectProfile) -> str:
    lines = [PROFILE_BLOCK_START, "## 探测到的项目栈 (managed by zf profile)", ""]
    lines.append(f"- layout: {profile.layout} · confidence: {profile.confidence}")
    for u in profile.units:
        fw = f" ({', '.join(u.frameworks)})" if u.frameworks else ""
        lines.append(
            f"- `{u.root}` — {u.language}{fw} · {u.surface}"
            f"{' · 有测试' if u.has_tests else ''}"
        )
    if profile.all_gate_cmds:
        lines.append("")
        lines.append(f"- 建议 gate: {', '.join(profile.all_gate_cmds)}")
    lines.append(PROFILE_BLOCK_END)
    return "\n".join(lines)


def apply_agents_md_stack(
    agents_md_path: str | Path, profile: ProjectProfile, *, write: bool = False
) -> dict:
    """Inject/replace the managed stack block in AGENTS.md (non-destructive)."""
    path = Path(agents_md_path)
    section = render_stack_section(profile)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if PROFILE_BLOCK_START in original and PROFILE_BLOCK_END in original:
        pre = original.split(PROFILE_BLOCK_START, 1)[0].rstrip()
        post = original.split(PROFILE_BLOCK_END, 1)[1].lstrip()
        updated = f"{pre}\n\n{section}\n\n{post}".rstrip() + "\n"
        action = "updated"
    else:
        sep = "\n\n" if original.strip() else ""
        updated = f"{original.rstrip()}{sep}{section}\n"
        action = "created"
    if write:
        path.write_text(updated, encoding="utf-8")
    return {"action": action, "section": section}
