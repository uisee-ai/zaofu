"""Render provider-neutral Skill activation instructions for worker briefings."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import RoleConfig


def render_skill_briefing_section(
    role: RoleConfig,
    skill_entries: list | None = None,
) -> list[str]:
    if not role.skills:
        return []

    entries_by_name = {
        getattr(entry, "name", ""): entry
        for entry in (skill_entries or [])
    }
    ordered_skills = list(role.skills)
    for entry in skill_entries or []:
        name = str(getattr(entry, "name", "") or "").strip()
        if name and name not in ordered_skills:
            ordered_skills.append(name)

    auto_lines: list[str] = []
    demand_lines: list[str] = []
    for skill in ordered_skills:
        entry = entries_by_name.get(skill)
        line = _render_skill_line(skill, entry)
        if entry is not None and bool(getattr(entry, "auto_inject", False)):
            auto_lines.append(line)
        else:
            demand_lines.append(line)

    lines = [
        "## Enabled Skills",
        "",
        "Use only the matching `zf.yaml` skills below. This index contains "
        "metadata, not skill bodies; full provenance remains in the runtime "
        "skills manifest.",
    ]
    if auto_lines:
        lines.extend([
            "",
            "### Auto-Injected Skills (Required)",
            "",
            "Invoke or read every skill in this subsection before substantive "
            "stage work. Their materialized `SKILL.md` bodies are active "
            "instructions.",
            *auto_lines,
        ])
    if demand_lines:
        lines.extend([
            "",
            "### Load-On-Demand Skills",
            "",
            "Load a skill in this subsection only when its description matches "
            "the current task.",
            *demand_lines,
        ])
    lines.append("")
    return lines


def recovery_skill_locators(*, project_root: Path, state_dir: Path, role: RoleConfig) -> str:
    """Give resumed sessions exact installed bodies, including older role indexes."""
    from zf.core.security.hash import sha256_file

    index_path = state_dir / "instructions" / f"{role.instance_id}.md"
    manifest_path = state_dir / "workdirs" / role.instance_id / "runtime" / "skills-manifest.json"
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
        indexed = {line.split("`", 2)[1]: line.partition("; dependency of: ")[0].removesuffix(")")
                   for line in lines if line.startswith("- `/") and "; mode: " in line}
        if not indexed:
            return ""
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(manifest, dict) or (manifest.get("instance_id"), manifest.get("backend")) != (
        role.instance_id, role.backend,
    ):
        return ""
    installed = manifest.get("skills")
    if not isinstance(installed, list):
        return ""
    locators = []
    for row in installed:
        if not isinstance(row, dict) or not isinstance(row.get("materialized_to"), str) or not row.get("sha256"):
            continue
        name = row.get("name")
        location = row["materialized_to"]
        suffix = f"; source: {row.get('source')}; runtime: {location}"
        body = project_root / location / "SKILL.md"
        if not indexed.get(f"/{name}", "").endswith((suffix, f"; read `{body}`")):
            continue
        try:
            digest = sha256_file(body)
        except OSError:
            continue
        if digest == row["sha256"]:
            locators.append(f"- `/{name}`: `{body}`")
    if not locators:
        return ""
    return ("\nCurrent installed skill read locators follow. "
            "Preserve the role index activation rules. Read applicable skills at these exact files "
            "even if an earlier locator failed.\n"
            + "\n".join(locators))


def auto_injected_skill_entries(skill_entries: list | None) -> list:
    return [
        entry
        for entry in (skill_entries or [])
        if bool(getattr(entry, "auto_inject", False))
    ]


def _render_skill_line(skill: str, entry: object | None) -> str:
    description = (
        str(getattr(entry, "description", "") or "").strip()
        if entry is not None else ""
    )
    status = (
        str(getattr(entry, "status", "") or "").strip()
        if entry is not None else ""
    )
    dependency_of = (
        tuple(getattr(entry, "dependency_of", ()) or ())
        if entry is not None else ()
    )
    suffix_parts = [status] if status else []
    if dependency_of:
        suffix_parts.append(f"dependency of: {', '.join(dependency_of)}")
    suffix = f" [{'; '.join(suffix_parts)}]" if suffix_parts else ""
    description_suffix = f" - {description}" if description else ""
    return f"- `/{skill}`{description_suffix}{suffix}"


__all__ = ["auto_injected_skill_entries", "render_skill_briefing_section"]
