"""Versioned built-in Agent Channel templates."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from zf.runtime.channel_contracts import (
    CHANNEL_PERMISSION_PROFILES,
    CHANNEL_ROLES,
    normalize_permission_profile,
    normalize_provider,
)


TEMPLATE_VERSION = "2026-07-31.1"
WRITER_PROFILES = {
    "artifact_writer",
    "project_writer",
    "workspace_writer",
    "isolated_writer",
    "dangerous_full",
}
ALLOWED_OVERRIDE_KEYS = {
    "backend",
    "model",
    "role_overrides",
    "writer_role",
    "writer_scope",
    "budget",
    "discussion_mode",
}
ALLOWED_ROLE_OVERRIDE_KEYS = {"backend", "model", "enabled", "profile_id"}
ALLOWED_BUDGET_KEYS = {"max_rounds", "max_parallel_replies", "phase_deadline_seconds"}
ALLOWED_DEADLINE_PHASES = {
    "phase1_blind",
    "phase2_relay",
    "phase3_synthesis",
}
PROFILE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _member(
    role: str,
    *,
    permission_profile: str = "read_only",
    skills: tuple[str, ...] = (),
    optional: bool = False,
) -> dict[str, Any]:
    context_name = role.replace("_", "-")
    skill_refs = list(
        dict.fromkeys(f"skills/{skill}/SKILL.md" for skill in skills if skill.strip())
    )
    return {
        "member_id": role,
        "channel_role": role,
        "permission_profile": permission_profile,
        "role_context_ref": f"channel_roles/{context_name}.md",
        "skill_refs": skill_refs,
        "optional": optional,
    }


CHANNEL_TEMPLATES: dict[str, dict[str, Any]] = {
    "prd-clarification": {
        "name": "PRD Clarification",
        "members": [
            _member(
                "product_pm",
                permission_profile="project_writer",
                skills=("zf-channel-discussion-participant",),
            ),
            _member("arch", skills=("zf-channel-discussion-participant",)),
            _member(
                "critic",
                skills=("zf-channel-discussion-participant",),
            ),
            _member(
                "synthesizer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-discussion-synthesizer",
                ),
            ),
            _member(
                "security_reviewer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-security-review",
                ),
                optional=True,
            ),
        ],
        "writer_roles": ["product_pm"],
        "writer_scope": ["docs/design/**", "docs/impl/**"],
        "discussion": {
            "mode": "conversation",
            "synthesizer": "synthesizer",
        },
    },
    "research-review": {
        "name": "Research Review",
        "members": [
            _member(
                "researcher",
                permission_profile="artifact_writer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-research-participant",
                    "zf-research-preflight-law",
                    "source-verification",
                ),
            ),
            _member(
                "arch",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-research-participant",
                    "zf-research-preflight-law",
                    "source-verification",
                ),
            ),
            _member(
                "critic",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-research-participant",
                    "zf-research-preflight-law",
                ),
            ),
            _member(
                "synthesizer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-research-synthesizer",
                    "zf-channel-discussion-synthesizer",
                    "zf-research-preflight-law",
                ),
            ),
        ],
        "writer_roles": ["researcher"],
        "writer_scope": [".zf/research/**", "/tmp/zf-research/**"],
        "discussion": {
            "mode": "conversation",
            "synthesizer": "synthesizer",
        },
    },
    "architecture-review": {
        "name": "Architecture Review",
        "members": [
            _member(
                "arch",
                permission_profile="project_writer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-discussion-synthesizer",
                    "zf-cr",
                    "zf-harness-design-impl-game-review",
                ),
            ),
            _member(
                "security_reviewer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-security-review",
                    "zf-harness-evidence-collection",
                ),
            ),
            _member(
                "dev_reviewer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-harness-design-impl-game-review",
                ),
            ),
            _member(
                "critic",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-harness-gate-evaluator",
                ),
            ),
        ],
        "writer_roles": ["arch"],
        "writer_scope": ["docs/design/**", "docs/impl/**"],
        "discussion": {
            "mode": "multi_lens",
            "synthesizer": "arch",
        },
    },
    "quick-change": {
        "name": "Quick Change",
        "members": [
            _member(
                "tech_leader",
                permission_profile="workspace_writer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-quick-change",
                    "zf-channel-discussion-synthesizer",
                ),
            ),
            _member(
                "dev_reviewer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-quick-change",
                ),
            ),
            _member(
                "qa_analyst",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-quick-change",
                    "zf-harness-verification-checklist",
                ),
            ),
        ],
        "writer_roles": ["tech_leader"],
        "writer_scope": ["**"],
        "discussion": {
            "mode": "conversation",
            "synthesizer": "tech_leader",
        },
    },
    "incident-triage": {
        "name": "Incident Triage",
        "members": [
            _member(
                "tech_leader",
                permission_profile="workspace_writer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-incident-triage",
                    "zf-channel-discussion-synthesizer",
                ),
            ),
            _member(
                "qa_analyst",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-incident-triage",
                    "zf-harness-evidence-collection",
                ),
            ),
            _member(
                "security_reviewer",
                skills=(
                    "zf-channel-discussion-participant",
                    "zf-channel-incident-triage",
                    "zf-channel-security-review",
                ),
                optional=True,
            ),
        ],
        "writer_roles": ["tech_leader"],
        "writer_scope": ["**"],
        "discussion": {
            "mode": "clarification",
            "synthesizer": "tech_leader",
        },
    },
}


def template_digest(template_id: str) -> str:
    template = CHANNEL_TEMPLATES[template_id]
    canonical = json.dumps(
        {"version": TEMPLATE_VERSION, "template": template},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def materialize_channel_template(
    template_id: str,
    *,
    overrides: object = None,
) -> tuple[dict[str, Any] | None, str]:
    if template_id not in CHANNEL_TEMPLATES:
        return None, f"unknown channel template: {template_id}"
    override_map = overrides if isinstance(overrides, dict) else {}
    unknown = sorted(set(override_map) - ALLOWED_OVERRIDE_KEYS)
    if unknown:
        return None, f"unsupported channel template override: {', '.join(unknown)}"

    result = deepcopy(CHANNEL_TEMPLATES[template_id])
    discussion_mode = str(override_map.get("discussion_mode") or "").strip()
    if discussion_mode:
        if discussion_mode not in {"conversation", "clarification", "multi_lens"}:
            return None, (
                "discussion_mode must be conversation, clarification, or multi_lens"
            )
        result.setdefault("discussion", {})["mode"] = discussion_mode
    members = result["members"]
    by_role = {str(member["channel_role"]): member for member in members}
    backend = str(override_map.get("backend") or "codex").strip()
    provider = normalize_provider(backend)
    if not provider:
        return None, f"unsupported channel backend: {backend}"
    model = str(override_map.get("model") or "").strip()
    role_overrides = override_map.get("role_overrides") or {}
    if not isinstance(role_overrides, dict):
        return None, "role_overrides must be a mapping"
    for role, raw in role_overrides.items():
        if role not in by_role or role not in CHANNEL_ROLES:
            return None, f"unknown template role override: {role}"
        if not isinstance(raw, dict):
            return None, f"role_overrides.{role} must be a mapping"
        unknown_role_keys = sorted(set(raw) - ALLOWED_ROLE_OVERRIDE_KEYS)
        if unknown_role_keys:
            return None, (
                f"unsupported role override for {role}: "
                + ", ".join(unknown_role_keys)
            )
        if "enabled" in raw and not bool(raw.get("enabled")):
            if not bool(by_role[role].get("optional")):
                return None, f"required template role cannot be disabled: {role}"
            by_role[role]["enabled"] = False
        if "profile_id" in raw:
            profile_id = str(raw.get("profile_id") or "").strip()
            if not PROFILE_ID_RE.fullmatch(profile_id):
                return None, (
                    f"role_overrides.{role}.profile_id must be a valid "
                    "Channel profile id"
                )
            by_role[role]["profile_id"] = profile_id
            by_role[role]["profile_selection_explicit"] = True
        role_backend = str(raw.get("backend") or backend).strip()
        if not normalize_provider(role_backend):
            return None, f"unsupported channel backend for {role}: {role_backend}"
        by_role[role]["backend"] = role_backend
        by_role[role]["provider"] = normalize_provider(role_backend)
        by_role[role]["model"] = str(raw.get("model") or model).strip()

    enabled_members: list[dict[str, Any]] = []
    for member in members:
        if member.get("enabled") is False:
            continue
        member.setdefault("backend", backend)
        member.setdefault("provider", provider)
        member.setdefault("model", model)
        member["member_type"] = "provider_agent"
        member.setdefault("profile_id", str(member["member_id"]))
        member["profile_revision"] = 1
        member["profile_provenance"] = "template_inline"
        member["permission_profile"] = "read_only"
        member["permission_ceiling"] = "read_only"
        member["permissions"] = ["read", "message", "summarize"]
        enabled_members.append(member)
    result["members"] = enabled_members

    writer_role = str(
        override_map.get("writer_role")
        or next(iter(result.get("writer_roles") or []), "")
    ).strip()
    if writer_role and writer_role not in result.get("writer_roles", []):
        return None, f"writer_role is not allowed by template: {writer_role}"
    writer_scope = override_map.get("writer_scope", result.get("writer_scope", []))
    if (
        not isinstance(writer_scope, list)
        or not writer_scope
        or not all(
            isinstance(item, str) and item.strip() for item in writer_scope
        )
    ):
        return None, "writer_scope must be a non-empty string list"
    leader_member_id = writer_role or str(
        result.get("discussion", {}).get("synthesizer") or ""
    )
    for member in enabled_members:
        role = str(member["channel_role"])
        profile = normalize_permission_profile(member.get("permission_profile"))
        member["writer_scope"] = []
        if str(member["member_id"]) == leader_member_id:
            member["permissions"] = [
                "read",
                "message",
                "summarize",
                "propose_workflow",
            ]
        if normalize_permission_profile(member["permission_profile"]) not in (
            CHANNEL_PERMISSION_PROFILES
        ):
            return None, f"unknown permission profile for role: {role}"
    budget = override_map.get("budget") or {}
    if not isinstance(budget, dict):
        return None, "budget must be a mapping"
    unknown_budget = sorted(set(budget) - ALLOWED_BUDGET_KEYS)
    if unknown_budget:
        return None, f"unsupported channel budget override: {', '.join(unknown_budget)}"
    max_rounds_explicit = budget.get("max_rounds") not in (None, "")
    max_rounds = 0
    if max_rounds_explicit:
        max_rounds, error = _bounded_budget_int(
            budget.get("max_rounds"),
            default=0,
            minimum=1,
            maximum=256,
            field="max_rounds",
        )
        if error:
            return None, error
    max_parallel_replies, error = _bounded_budget_int(
        budget.get("max_parallel_replies"),
        default=len(enabled_members),
        minimum=1,
        maximum=max(1, len(enabled_members)),
        field="max_parallel_replies",
    )
    if error:
        return None, error
    deadlines, error = _phase_deadlines(
        budget.get("phase_deadline_seconds")
    )
    if error:
        return None, error
    discussion = result["discussion"]
    discussion["participants"] = [
        str(member["member_id"]) for member in enabled_members
    ]
    discussion["default_responder_id"] = str(
        discussion.get("default_responder_id")
        or discussion.get("synthesizer")
        or writer_role
        or ""
    )
    discussion["max_rounds"] = max_rounds
    discussion["max_rounds_explicit"] = max_rounds_explicit
    discussion["max_parallel_replies"] = max_parallel_replies
    if deadlines:
        discussion["phase_deadline_seconds"] = deadlines
    materialization_digest = _template_materialization_digest(
        template_id,
        result,
    )
    result.update({
        "template_id": template_id,
        "template_version": TEMPLATE_VERSION,
        "template_digest": template_digest(template_id),
        "materialization_digest": materialization_digest,
        "writer_role": writer_role,
        "writer_scope": list(writer_scope),
        "leader_member_id": leader_member_id,
    })
    return result, ""


def _bounded_budget_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    field: str,
) -> tuple[int, str]:
    if value in (None, ""):
        return default, ""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0, f"budget.{field} must be an integer"
    if parsed < minimum or parsed > maximum:
        return 0, (
            f"budget.{field} must be between {minimum} and {maximum}"
        )
    return parsed, ""


def _template_materialization_digest(
    template_id: str,
    materialized: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "template_id": template_id,
            "version": TEMPLATE_VERSION,
            "materialized": materialized,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _phase_deadlines(value: object) -> tuple[dict[str, int], str]:
    if value in (None, ""):
        return {}, ""
    if not isinstance(value, dict):
        return {}, "budget.phase_deadline_seconds must be a mapping"
    unknown = sorted(set(value) - ALLOWED_DEADLINE_PHASES)
    if unknown:
        return {}, (
            "unsupported phase deadline: " + ", ".join(unknown)
        )
    deadlines: dict[str, int] = {}
    for phase, raw in value.items():
        parsed, error = _bounded_budget_int(
            raw,
            default=0,
            minimum=1,
            maximum=86400,
            field=f"phase_deadline_seconds.{phase}",
        )
        if error:
            return {}, error
        deadlines[str(phase)] = parsed
    return deadlines, ""


__all__ = [
    "CHANNEL_TEMPLATES",
    "TEMPLATE_VERSION",
    "materialize_channel_template",
    "template_digest",
]
