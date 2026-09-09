"""Project profile binding and immutable Channel member snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from zf.runtime.channel_contracts import (
    CHANNEL_PERMISSION_PROFILES,
    CHANNEL_VISIBILITY_PROFILES,
    normalize_channel_role,
    normalize_channel_skill_refs,
    normalize_member_type,
    normalize_permission_profile,
    normalize_permissions,
    normalize_provider,
    normalize_visibility_profile,
    permission_profile_write_policy,
)
from zf.runtime.channel_roles import (
    ROLE_CONTEXT_MAX_CHARS,
    load_role_definition_excerpt,
    normalize_role_context_ref,
)
from zf.runtime.sidecar_refs import (
    SidecarRefError,
    safe_sidecar_ref,
    sidecar_path,
    write_sidecar_json,
)


CHANNEL_PROFILE_SNAPSHOT_SCHEMA_VERSION = "channel.agent_profile_snapshot.v2"
_PROFILE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_VISIBILITY_RANK = {
    "minimal": 0,
    "planner": 1,
    "reviewer": 1,
    "owner_report": 2,
    "full_audit": 3,
}
_PERMISSION_RANK = {
    "read_only": 0,
    "operator": 1,
    "artifact_writer": 2,
    "project_writer": 3,
    "workspace_writer": 4,
    "isolated_writer": 4,
    "dangerous_full": 5,
}


def bind_channel_member_profile(
    config: Any,
    payload: dict[str, Any],
    *,
    allow_inline_profile: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve one member against the project profile catalog.

    The result is a complete immutable binding. Runtime validates ceilings and
    identity but does not assess persona or skill quality.
    """

    profile_id = str(payload.get("profile_id") or "").strip()
    catalog = getattr(getattr(config, "channel", None), "agent_profiles", {})
    profile = catalog.get(profile_id) if profile_id and isinstance(catalog, dict) else None
    if profile_id and profile is None and not allow_inline_profile:
        return None, f"unknown channel agent profile: {profile_id}"
    if profile_id and not _PROFILE_ID_RE.match(profile_id):
        return None, "profile_id must be a valid Channel profile id"

    if profile is None:
        binding = _legacy_or_inline_binding(
            payload,
            profile_id=profile_id,
            provenance="template_inline" if allow_inline_profile and profile_id else "legacy_inline",
        )
        return binding, ""

    member_type = normalize_member_type(
        payload.get("member_type") or "provider_agent",
        backend=payload.get("provider") or payload.get("backend") or profile.provider,
    )
    channel_role = normalize_channel_role(
        profile.channel_role,
        member_type=member_type,
    )
    requested_role = str(payload.get("channel_role") or payload.get("role") or "").strip()
    if requested_role and normalize_channel_role(
        requested_role,
        member_type=member_type,
    ) != channel_role:
        return None, "member channel_role cannot override its profile"

    permission_ceiling = normalize_permission_profile(profile.permission_ceiling)
    permission_profile = normalize_permission_profile(
        payload.get("permission_profile") or permission_ceiling
    )
    if not _within_permission_ceiling(permission_profile, permission_ceiling):
        return None, (
            f"permission_profile {permission_profile!r} exceeds profile ceiling "
            f"{permission_ceiling!r}"
        )
    visibility_ceiling = str(profile.visibility_ceiling or "minimal")
    visibility_profile = normalize_visibility_profile(
        payload.get("visibility_profile") or visibility_ceiling,
        channel_role=channel_role,
        member_type=member_type,
    )
    if not _within_visibility_ceiling(visibility_profile, visibility_ceiling):
        return None, (
            f"visibility_profile {visibility_profile!r} exceeds profile ceiling "
            f"{visibility_ceiling!r}"
        )

    profile_skills = normalize_channel_skill_refs(profile.skill_refs)
    requested_skills = normalize_channel_skill_refs(payload.get("skill_refs"))
    if requested_skills and not set(requested_skills).issubset(profile_skills):
        return None, "member skill_refs must be a subset of its profile skill_refs"
    skill_refs = requested_skills or profile_skills
    provider = normalize_provider(
        payload.get("provider") or payload.get("backend") or profile.provider or profile.backend
    )
    expected_provider = normalize_provider(profile.provider or profile.backend)
    if expected_provider and provider != expected_provider:
        return None, "member provider cannot override its profile"
    backend = str(payload.get("backend") or profile.backend or provider).strip()
    if profile.backend and backend != str(profile.backend):
        return None, "member backend cannot override its profile"
    model = str(payload.get("model") or profile.model or "").strip()
    if profile.model and model != str(profile.model):
        return None, "member model cannot override its profile"
    role_context_ref = str(
        payload.get("role_context_ref") or profile.role_context_ref or ""
    ).strip()
    if profile.role_context_ref and role_context_ref != str(profile.role_context_ref):
        return None, "member role_context_ref cannot override its profile"

    binding = {
        **payload,
        "profile_id": profile_id,
        "profile_revision": int(profile.revision),
        "profile_provenance": "project_catalog",
        "persona": str(payload.get("persona") or profile.persona or profile_id),
        "display_name": str(
            payload.get("display_name")
            or profile.display_name
            or profile.persona
            or profile_id
        ),
        "member_type": member_type,
        "channel_role": channel_role,
        "role": channel_role,
        "provider": provider,
        "backend": backend,
        "model": model,
        "role_context_ref": role_context_ref,
        "skill_refs": skill_refs,
        "visibility_profile": visibility_profile,
        "visibility_ceiling": visibility_ceiling,
        "permission_profile": permission_profile,
        "permission_ceiling": permission_ceiling,
        "permissions": normalize_permissions(
            payload.get("permissions"),
            member_type=member_type,
        ),
        "lifecycle": str(profile.lifecycle or "persistent"),
    }
    return _with_binding_digests(binding), ""


def template_profile_binding_payload(
    member: dict[str, Any],
    *,
    template_id: str,
) -> dict[str, Any]:
    """Prepare one fixed template role for catalog-profile binding.

    A template defines the role slot and mechanical permissions. An explicitly
    selected project Profile owns the semantic execution identity, so template
    backend/model/context/skill defaults must not masquerade as overrides of
    that Profile.
    """

    payload = {**member, "template_id": template_id}
    if payload.get("profile_selection_explicit") is True:
        for key in (
            "backend",
            "provider",
            "model",
            "role_context_ref",
            "skill_refs",
        ):
            payload.pop(key, None)
    return payload


def channel_profile_selection_manifest(
    config: Any,
    *,
    template_id: str,
    members: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str, str]:
    """Return the public, exact Profile bindings a template would use.

    The manifest is safe for a Kanban Plan: it contains no secret or prompt
    body, while its digest makes profile revision/provider/skill drift visible
    at the controlled action boundary.
    """

    rows: list[dict[str, Any]] = []
    for raw_member in members:
        if not isinstance(raw_member, dict):
            return [], "", "channel template member must be a mapping"
        binding, error = bind_channel_member_profile(
            config,
            template_profile_binding_payload(raw_member, template_id=template_id),
            allow_inline_profile=not bool(
                raw_member.get("profile_selection_explicit")
            ),
        )
        if error or binding is None:
            return [], "", error or "channel template profile binding failed"
        rows.append({
            "member_id": str(binding.get("member_id") or ""),
            "channel_role": str(binding.get("channel_role") or ""),
            "profile_id": str(binding.get("profile_id") or ""),
            "profile_revision": int(binding.get("profile_revision") or 1),
            "profile_provenance": str(
                binding.get("profile_provenance") or ""
            ),
            "persona": str(binding.get("persona") or ""),
            "display_name": str(binding.get("display_name") or ""),
            "provider": str(binding.get("provider") or ""),
            "backend": str(binding.get("backend") or ""),
            "model": str(binding.get("model") or ""),
            "skill_refs": list(binding.get("skill_refs") or []),
            "profile_digest": str(binding.get("profile_digest") or ""),
            "config_digest": str(binding.get("config_digest") or ""),
        })
    return rows, _stable_digest(rows), ""


def public_channel_profile_catalog(config: Any) -> list[dict[str, Any]]:
    """Expose project-owned Profile identity to a Kanban Agent without secrets."""

    catalog = getattr(getattr(config, "channel", None), "agent_profiles", {})
    if not isinstance(catalog, dict):
        return []
    rows: list[dict[str, Any]] = []
    for profile_id, profile in sorted(catalog.items()):
        rows.append({
            "profile_id": str(profile_id),
            "revision": int(getattr(profile, "revision", 1) or 1),
            "channel_role": str(getattr(profile, "channel_role", "") or ""),
            "persona": str(getattr(profile, "persona", "") or ""),
            "display_name": str(getattr(profile, "display_name", "") or ""),
            "provider": str(getattr(profile, "provider", "") or ""),
            "backend": str(getattr(profile, "backend", "") or ""),
            "model": str(getattr(profile, "model", "") or ""),
            "role_context_ref": str(
                getattr(profile, "role_context_ref", "") or ""
            ),
            "skill_refs": list(getattr(profile, "skill_refs", []) or []),
            "lifecycle": str(getattr(profile, "lifecycle", "") or ""),
        })
    return rows


def write_channel_profile_snapshot(
    state_dir: Path,
    *,
    channel_id: str,
    member_id: str,
    binding: dict[str, Any],
    resolved_skill_refs: list[dict[str, str]],
    role_definition: dict[str, str] | None,
    created_by: str,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Persist the exact member contract used by provider dispatch."""

    snapshot = {
        "schema_version": CHANNEL_PROFILE_SNAPSHOT_SCHEMA_VERSION,
        "channel_id": channel_id,
        "member_id": member_id,
        "profile_id": str(binding.get("profile_id") or ""),
        "profile_revision": int(binding.get("profile_revision") or 1),
        "profile_provenance": str(binding.get("profile_provenance") or "legacy_inline"),
        "profile_digest": str(binding.get("profile_digest") or ""),
        "config_digest": str(binding.get("config_digest") or ""),
        "skill_set_digest": _stable_digest(
            [
                {
                    "logical_ref": str(item.get("logical_ref") or ""),
                    "sha256": str(item.get("sha256") or ""),
                }
                for item in resolved_skill_refs
                if isinstance(item, dict)
            ]
        ),
        "permission_digest": str(binding.get("permission_digest") or ""),
        "role_definition_digest": str(
            (role_definition or {}).get("sha256") or ""
        ),
        "binding": {
            key: binding.get(key)
            for key in (
                "persona",
                "display_name",
                "member_type",
                "channel_role",
                "provider",
                "backend",
                "model",
                "role_context_ref",
                "skill_refs",
                "visibility_profile",
                "visibility_ceiling",
                "permission_profile",
                "permission_ceiling",
                "permissions",
                "lifecycle",
            )
        },
        "role_definition": dict(role_definition or {}),
        "resolved_skill_refs": resolved_skill_refs,
    }
    snapshot["snapshot_digest"] = _stable_digest(snapshot)
    ref = (
        PurePosixPath("channels")
        / _safe_segment(channel_id)
        / "profiles"
        / (
            f"{_safe_segment(member_id)}-r{snapshot['profile_revision']}-"
            f"{snapshot['snapshot_digest'][:16]}.json"
        )
    )
    descriptor = write_sidecar_json(
        state_dir,
        ref,
        snapshot,
        kind="channel_agent_profile_snapshot",
        schema_version=CHANNEL_PROFILE_SNAPSHOT_SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "member_id": member_id,
        },
        retention={"class": "audit_required", "redaction_profile": "default"},
        required=True,
        preview=(
            f"{snapshot['profile_id'] or 'legacy-inline'} "
            f"r{snapshot['profile_revision']}"
        ),
    )
    return {
        **descriptor,
        "profile_digest": snapshot["profile_digest"],
        "config_digest": snapshot["config_digest"],
        "skill_set_digest": snapshot["skill_set_digest"],
        "permission_digest": snapshot["permission_digest"],
        "role_definition_digest": snapshot["role_definition_digest"],
        "snapshot_digest": snapshot["snapshot_digest"],
    }


def resolve_channel_role_definition(
    binding: dict[str, Any],
    *,
    project_root: Path,
) -> tuple[dict[str, str], str]:
    """Resolve the exact role body before a member becomes durable."""

    ref = normalize_role_context_ref(binding.get("role_context_ref"))
    if not ref:
        return {}, ""
    definition = load_role_definition_excerpt(
        ref,
        repo_root=project_root,
        max_chars=ROLE_CONTEXT_MAX_CHARS,
        fallback_to_builtin=(
            str(binding.get("profile_provenance") or "")
            != "project_catalog"
        ),
    )
    if str(definition.get("status") or "") != "loaded":
        return {}, f"channel role definition is missing: {ref}"
    return definition, ""


def load_channel_profile_role_definition(
    state_dir: Path,
    binding: dict[str, Any],
) -> dict[str, str]:
    """Hydrate and verify the immutable role body pinned by a member."""

    ref = str(binding.get("profile_snapshot_ref") or "").strip()
    expected_file_digest = str(
        binding.get("profile_snapshot_sha256") or ""
    ).strip()
    if not ref:
        raise SidecarRefError(
            "ref_missing",
            "channel member profile snapshot ref is missing",
        )
    path = sidecar_path(state_dir, safe_sidecar_ref(ref))
    if not path.is_file():
        raise SidecarRefError(
            "ref_missing",
            f"channel member profile snapshot is missing: {ref}",
            ref=ref,
        )
    raw = path.read_bytes()
    actual_file_digest = hashlib.sha256(raw).hexdigest()
    if expected_file_digest and expected_file_digest != actual_file_digest:
        raise SidecarRefError(
            "hash_mismatch",
            f"channel member profile snapshot digest mismatch: {ref}",
            ref=ref,
        )
    try:
        snapshot = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SidecarRefError(
            "schema_unsupported",
            f"channel member profile snapshot is invalid: {ref}",
            ref=ref,
        ) from exc
    if not isinstance(snapshot, dict):
        raise SidecarRefError(
            "schema_unsupported",
            f"channel member profile snapshot must be an object: {ref}",
            ref=ref,
        )
    expected_snapshot_digest = str(snapshot.get("snapshot_digest") or "")
    semantic_snapshot = {
        key: value
        for key, value in snapshot.items()
        if key != "snapshot_digest"
    }
    if (
        not expected_snapshot_digest
        or _stable_digest(semantic_snapshot) != expected_snapshot_digest
    ):
        raise SidecarRefError(
            "hash_mismatch",
            f"channel member profile snapshot semantic digest mismatch: {ref}",
            ref=ref,
        )
    definition = snapshot.get("role_definition")
    if not isinstance(definition, dict):
        definition = {}
    role_ref = normalize_role_context_ref(binding.get("role_context_ref"))
    if role_ref and (
        str(definition.get("status") or "") != "loaded"
        or str(definition.get("role_context_ref") or "") != role_ref
        or not str(definition.get("sha256") or "")
    ):
        raise SidecarRefError(
            "schema_unsupported",
            f"channel member profile snapshot lacks its role definition: {ref}",
            ref=ref,
        )
    return {
        str(key): str(value)
        for key, value in definition.items()
        if value is not None
    }


def _legacy_or_inline_binding(
    payload: dict[str, Any],
    *,
    profile_id: str,
    provenance: str,
) -> dict[str, Any]:
    member_type = normalize_member_type(
        payload.get("member_type"),
        backend=payload.get("provider") or payload.get("backend"),
    )
    channel_role = normalize_channel_role(
        payload.get("channel_role") or payload.get("role"),
        member_type=member_type,
    )
    binding = {
        **payload,
        "profile_id": profile_id,
        "profile_revision": int(payload.get("profile_revision") or 1),
        "profile_provenance": provenance,
        "persona": str(payload.get("persona") or payload.get("member_id") or ""),
        "display_name": str(
            payload.get("display_name")
            or payload.get("persona")
            or payload.get("member_id")
            or ""
        ),
        "member_type": member_type,
        "channel_role": channel_role,
        "role": str(payload.get("role") or channel_role),
        "provider": normalize_provider(
            payload.get("provider") or payload.get("backend")
        ),
        "backend": str(payload.get("backend") or payload.get("provider") or ""),
        "model": str(payload.get("model") or ""),
        "role_context_ref": str(payload.get("role_context_ref") or ""),
        "skill_refs": normalize_channel_skill_refs(payload.get("skill_refs")),
        "visibility_profile": normalize_visibility_profile(
            payload.get("visibility_profile"),
            channel_role=channel_role,
            member_type=member_type,
        ),
        "visibility_ceiling": str(
            payload.get("visibility_ceiling")
            or payload.get("visibility_profile")
            or "full_audit"
        ),
        "permission_profile": normalize_permission_profile(
            payload.get("permission_profile")
        ),
        "permission_ceiling": str(
            payload.get("permission_ceiling")
            or payload.get("permission_profile")
            or "dangerous_full"
        ),
        "permissions": normalize_permissions(
            payload.get("permissions"),
            member_type=member_type,
        ),
        "lifecycle": str(payload.get("lifecycle") or "persistent"),
    }
    return _with_binding_digests(binding)


def _with_binding_digests(binding: dict[str, Any]) -> dict[str, Any]:
    result = dict(binding)
    result["profile_digest"] = _stable_digest({
        key: result.get(key)
        for key in (
            "profile_id",
            "profile_revision",
            "persona",
            "display_name",
            "channel_role",
            "lifecycle",
        )
    })
    result["config_digest"] = _stable_digest({
        key: result.get(key)
        for key in (
            "provider",
            "backend",
            "model",
            "role_context_ref",
            "visibility_profile",
        )
    })
    result["skill_set_digest"] = _stable_digest(result.get("skill_refs") or [])
    result["permission_digest"] = _stable_digest({
        "permission_profile": result.get("permission_profile"),
        "permission_ceiling": result.get("permission_ceiling"),
        "permissions": result.get("permissions") or [],
        "write_policy": permission_profile_write_policy(
            result.get("permission_profile")
        ),
    })
    return result


def _within_permission_ceiling(value: str, ceiling: str) -> bool:
    if value not in CHANNEL_PERMISSION_PROFILES or ceiling not in CHANNEL_PERMISSION_PROFILES:
        return False
    if _PERMISSION_RANK[value] > _PERMISSION_RANK[ceiling]:
        return False
    if value == "isolated_writer" and ceiling == "workspace_writer":
        return False
    if value == "workspace_writer" and ceiling == "isolated_writer":
        return False
    return True


def _within_visibility_ceiling(value: str, ceiling: str) -> bool:
    if value not in CHANNEL_VISIBILITY_PROFILES or ceiling not in CHANNEL_VISIBILITY_PROFILES:
        return False
    return _VISIBILITY_RANK[value] <= _VISIBILITY_RANK[ceiling]


def _stable_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_segment(value: object) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())[:96]
    return cleaned.strip(".-") or "unknown"


__all__ = [
    "CHANNEL_PROFILE_SNAPSHOT_SCHEMA_VERSION",
    "bind_channel_member_profile",
    "channel_profile_selection_manifest",
    "load_channel_profile_role_definition",
    "public_channel_profile_catalog",
    "resolve_channel_role_definition",
    "template_profile_binding_payload",
    "write_channel_profile_snapshot",
]
