"""Apply a profile/recommendation onto project files (doc 102 §4.3 wire-up).

Three sinks: materialize a fresh zf.yaml from the chosen archetype, fill
``quality_gates.static.required_checks`` no-clobber (PB5), and inject a managed
"detected stack" section into AGENTS.md non-destructively (PB6). YAML round-trip
is acceptable for the materialize path (file is freshly generated); the
no-clobber path only ever *adds* when the list is empty.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

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
            return text  # validated prod flow — copy as-is
        archetype = "minimal"  # flow yaml unavailable → safe fallback
    preset = copy.deepcopy(get_preset(archetype))
    qg = preset.setdefault("quality_gates", {}).setdefault("static", {})
    if not qg.get("required_checks"):
        qg["required_checks"] = list(recommendation.required_checks)
    if recommendation.harness_profile and recommendation.harness_profile != "baseline":
        preset.setdefault("workflow", {})["harness_profile"] = recommendation.harness_profile
    text = yaml.dump(preset, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return text.replace("{project_name}", project_name)


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
