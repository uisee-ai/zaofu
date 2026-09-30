"""Fail-closed revision binding shared by Issue assessment and delivery."""
from __future__ import annotations
from typing import Any

REVISION_FIELDS = ("reported_source_revision", "evaluated_against", "fix_based_on", "candidate_head", "pr_head", "verification_revision", "merge_sha")

def normalize_version_context(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    aliases = {"evaluated_against": "evaluated_source_revision", "fix_based_on": "fix_base_revision", "candidate_head": "candidate_head_revision", "pr_head": "pr_head_revision", "merge_sha": "merge_revision"}
    return {field: str(raw.get(field) or raw.get(aliases.get(field, "")) or "").strip().lower() for field in REVISION_FIELDS} | {
        "reported_project_version": str(raw.get("reported_project_version") or "").strip(),
        "reproduction_artifact_version": str(raw.get("reproduction_artifact_version") or "").strip(),
        "source_match_status": str(raw.get("source_match_status") or "unknown").strip(),
    }

def validate_delivery_version_context(value: Any, *, require_pr: bool = False, require_merge: bool = False) -> tuple[bool, list[str]]:
    context = normalize_version_context(value)
    missing = [field for field in ("evaluated_against", "fix_based_on", "candidate_head") if not context[field]]
    if require_pr and not context["pr_head"]: missing.append("pr_head")
    if require_merge and not context["merge_sha"]: missing.append("merge_sha")
    mismatches: list[str] = []
    if context["pr_head"] and context["pr_head"] != context["candidate_head"]: mismatches.append("pr_head!=candidate_head")
    if context["verification_revision"] and context["pr_head"] and context["verification_revision"] != context["pr_head"]: mismatches.append("verification_revision!=pr_head")
    return (not missing and not mismatches), [*missing, *mismatches]
