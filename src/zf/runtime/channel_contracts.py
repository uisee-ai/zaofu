"""Shared Agent Channel identity and role contracts."""

from __future__ import annotations

import re
from typing import Any

from zf.runtime.channel_roles import validate_role_context_ref


CHANNEL_MEMBER_TYPES = {
    "human",
    "provider_agent",
    "persona_agent",
    "owner_delegate",
    "runtime_role_binding",
    "observer",
    "automation_reporter",
}
LEGACY_MEMBER_TYPE_ALIASES = {
    "persona": "persona_agent",
    "readonly-reviewer": "observer",
    "runtime-role": "runtime_role_binding",
    "codex": "provider_agent",
    "claude-code": "provider_agent",
    "hermes": "provider_agent",
    "openclaw": "provider_agent",
}
CHANNEL_MEMBER_TYPE_INPUTS = CHANNEL_MEMBER_TYPES | set(LEGACY_MEMBER_TYPE_ALIASES)
LEGACY_PROVIDER_MEMBER_TYPES = {"codex", "claude-code", "hermes", "openclaw"}

CHANNEL_PROVIDERS = {"codex", "claude-code", "hermes", "openclaw", "fake", "runtime-role"}
CHANNEL_ROLES = {
    "arch",
    "facilitator",
    "tech_leader",
    "product_pm",
    "researcher",
    "synthesizer",
    "security_reviewer",
    "qa_analyst",
    "dev_reviewer",
    "critic",
    "spine_reviewer",
    "owner_delegate",
    "automation_reporter",
    "observer",
}
CHANNEL_VISIBILITY_PROFILES = {"minimal", "planner", "reviewer", "owner_report", "full_audit"}
CHANNEL_PERMISSION_PROFILES = {
    "read_only",
    "artifact_writer",
    "project_writer",
    "dangerous_full",
}
CHANNEL_PERMISSION_PROFILE_DEFAULT = "read_only"
CHANNEL_PERMISSION_PROFILE_WRITE_POLICY = {
    "read_only": {
        "mode": "read_only",
        "allowed_write_paths": [],
        "requires_gate": True,
    },
    "artifact_writer": {
        "mode": "artifact_writer",
        "allowed_write_paths": [
            ".zf/channel-artifacts/",
            ".zf/research/",
            "/tmp/zf-research/",
        ],
        "requires_gate": True,
    },
    "project_writer": {
        "mode": "project_writer",
        "allowed_write_paths": [
            "docs/design/",
            "docs/plans/",
            "docs/impl/",
            "skills/",
            "tasks/",
            "backlogs/",
        ],
        "requires_gate": True,
    },
    "dangerous_full": {
        "mode": "dangerous_full",
        "allowed_write_paths": ["*"],
        "requires_gate": True,
        "dangerous": True,
    },
}
CHANNEL_SAFE_PERMISSIONS = {
    "read",
    "message",
    "summarize",
    "propose_workflow",
    "read_reports",
    "report_owner",
}
CHANNEL_DISCUSSION_MODES = {
    "manual_mention",
    "mention_relay",
    "round_robin",
    "priority",
    "leader_delegation",
    "fanout_then_synthesis",
    "debate_judge",
}
CHANNEL_PROVIDER_BINDING_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
CHANNEL_SKILL_REF_RE = re.compile(r"^skills/[A-Za-z0-9_.-]+/SKILL\.md$")
CHANNEL_ROLE_VISIBILITY_DEFAULTS = {
    "arch": "planner",
    "facilitator": "planner",
    "tech_leader": "planner",
    "product_pm": "planner",
    "researcher": "minimal",
    "synthesizer": "planner",
    "security_reviewer": "reviewer",
    "qa_analyst": "reviewer",
    "dev_reviewer": "reviewer",
    "critic": "reviewer",
    "spine_reviewer": "reviewer",
    "owner_delegate": "owner_report",
    "automation_reporter": "owner_report",
    "observer": "minimal",
}
CHANNEL_MEMBER_ROLE_DEFAULTS = {
    "owner_delegate": "owner_delegate",
    "automation_reporter": "automation_reporter",
    "observer": "observer",
}


def normalize_member_type(value: object, *, backend: object = "") -> str:
    raw = str(value or "").strip()
    if raw in CHANNEL_MEMBER_TYPES:
        return raw
    if raw in LEGACY_MEMBER_TYPE_ALIASES:
        return LEGACY_MEMBER_TYPE_ALIASES[raw]
    provider = normalize_provider(backend or raw)
    if provider == "runtime-role":
        return "runtime_role_binding"
    if provider:
        return "provider_agent"
    return ""


def normalize_provider(value: object) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw == "claude":
        return "claude-code"
    if raw in CHANNEL_PROVIDERS:
        return raw
    return ""


def normalize_channel_role(value: object, *, member_type: str = "") -> str:
    raw = _normalize_role_token(value)
    if raw in CHANNEL_ROLES:
        return raw
    return CHANNEL_MEMBER_ROLE_DEFAULTS.get(member_type, "dev_reviewer")


def normalize_visibility_profile(value: object, *, channel_role: str = "", member_type: str = "") -> str:
    raw = str(value or "").strip()
    if raw in CHANNEL_VISIBILITY_PROFILES:
        return raw
    if channel_role in CHANNEL_ROLE_VISIBILITY_DEFAULTS:
        return CHANNEL_ROLE_VISIBILITY_DEFAULTS[channel_role]
    if member_type == "owner_delegate":
        return "owner_report"
    return "minimal"


def normalize_permission_profile(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in CHANNEL_PERMISSION_PROFILES:
        return raw
    return CHANNEL_PERMISSION_PROFILE_DEFAULT


def default_debate_max_rounds(member_count: int) -> int:
    """G4's round budget (channel_router._debate_round_guard_reason) is
    consumed by dispatched reply_requests, not agent-to-agent relay hops —
    a single @all fanout to N roster members alone consumes N "rounds". A
    fixed default of 6 exhausts before a 3-member discussion's blind fanout
    + phase2-relay-kickoff + synthesis-kickoff (3+3+1=7 dispatches) even
    completes, permanently blocking synthesis dispatch (2026-07-03
    racing-codex e2e rounds 1-3, 100% reproduction with the 3-member/6-round
    combination). Scale with roster size so the documented discussion
    protocol's own structured dispatches always fit, leaving headroom for
    real relay chatter.
    """
    return max(6, max(member_count, 1) * 4)


def permission_profile_write_policy(value: object) -> dict[str, Any]:
    profile = normalize_permission_profile(value)
    return dict(CHANNEL_PERMISSION_PROFILE_WRITE_POLICY[profile])


def normalize_channel_skill_refs(value: object, *, max_refs: int = 8) -> list[str]:
    refs: list[str] = []
    for raw in _string_list(value):
        item = raw.strip().replace("\\", "/")
        if item.startswith("./"):
            item = item[2:]
        if not item:
            continue
        if "/" not in item and item not in {".", ".."}:
            item = f"skills/{item}/SKILL.md"
        elif item.startswith("skills/") and item.endswith(".md") and not item.endswith("/SKILL.md"):
            name = item.removeprefix("skills/").removesuffix(".md")
            if "/" not in name:
                item = f"skills/{name}/SKILL.md"
        if CHANNEL_SKILL_REF_RE.match(item) and item not in refs:
            refs.append(item)
        if len(refs) >= max_refs:
            break
    return refs


def normalize_permissions(value: object, *, member_type: str = "") -> list[str]:
    raw = _string_list(value)
    if raw:
        return [item for item in raw if item in CHANNEL_SAFE_PERMISSIONS]
    if member_type in {"owner_delegate", "automation_reporter"}:
        return ["read", "summarize", "read_reports", "report_owner"]
    if member_type == "observer":
        return ["read", "summarize"]
    return ["read", "message", "summarize", "propose_workflow"]


def validate_channel_member_contract(payload: dict[str, Any]) -> str:
    member_type_raw = str(payload.get("member_type") or "").strip()
    if member_type_raw and member_type_raw not in CHANNEL_MEMBER_TYPE_INPUTS:
        return "member_type must be one of " + ", ".join(sorted(CHANNEL_MEMBER_TYPE_INPUTS))
    provider = str(payload.get("provider") or "").strip()
    if provider and normalize_provider(provider) not in CHANNEL_PROVIDERS:
        return "provider must be one of " + ", ".join(sorted(CHANNEL_PROVIDERS))
    provider_binding_id = str(
        payload.get("provider_binding_id")
        or payload.get("binding_id")
        or payload.get("binding")
        or ""
    ).strip()
    if provider_binding_id and not CHANNEL_PROVIDER_BINDING_RE.match(provider_binding_id):
        return "provider_binding_id must start with a letter and contain only letters, digits, dot, underscore, or hyphen"
    channel_role = str(payload.get("channel_role") or "").strip()
    if channel_role and _normalize_role_token(channel_role) not in CHANNEL_ROLES:
        return "channel_role must be one of " + ", ".join(sorted(CHANNEL_ROLES))
    visibility = str(payload.get("visibility_profile") or "").strip()
    if visibility and visibility not in CHANNEL_VISIBILITY_PROFILES:
        return "visibility_profile must be one of " + ", ".join(sorted(CHANNEL_VISIBILITY_PROFILES))
    permission_profile = str(payload.get("permission_profile") or "").strip()
    normalized_permission_profile = (
        permission_profile.lower().replace("-", "_").replace(" ", "_")
    )
    if permission_profile and normalized_permission_profile not in CHANNEL_PERMISSION_PROFILES:
        return "permission_profile must be one of " + ", ".join(sorted(CHANNEL_PERMISSION_PROFILES))
    if normalize_permission_profile(permission_profile) == "dangerous_full" and not _truthy(
        payload.get("dangerous_ack")
        or payload.get("permission_profile_ack")
        or payload.get("confirm_dangerous")
    ):
        return "permission_profile dangerous_full requires dangerous_ack=true"
    ref_error = validate_role_context_ref(payload.get("role_context_ref"))
    if ref_error:
        return ref_error
    skill_refs = payload.get("skill_refs")
    if skill_refs is not None:
        if not isinstance(skill_refs, str | list | tuple | set):
            return "skill_refs must be a comma string or a list of project skill refs"
        raw_refs = _string_list(skill_refs)
        normalized_refs = normalize_channel_skill_refs(skill_refs, max_refs=max(len(raw_refs), 1))
        if len(normalized_refs) != len({ref for ref in raw_refs if ref.strip()}):
            return "skill_refs must be repo-local project skill refs like skills/<name>/SKILL.md"
    permissions = payload.get("permissions")
    if permissions is not None:
        if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
            return "permissions must be a list of strings"
        unsafe = sorted(set(permissions) - CHANNEL_SAFE_PERMISSIONS)
        if unsafe:
            return "permissions must be safe channel permissions only; unsafe: " + ", ".join(unsafe)
    binding = payload.get("workflow_role_binding")
    if binding is not None and not isinstance(binding, dict):
        return "workflow_role_binding must be an object"
    return ""


def _normalize_role_token(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    return text


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
