"""Resolve the configured semantic Orchestrator Agent role.

The legacy role name is kept as the fallback for 194 configurations.  An
explicit ``workflow.orchestration.oa_profile`` binds only the semantic OA
transport and operation identity; it does not by itself activate authority.
"""

from __future__ import annotations

from typing import Any


LEGACY_ORCHESTRATOR_ROLE = "orchestrator"


def orchestrator_profile_name(runtime: Any) -> str:
    orchestration = getattr(
        getattr(getattr(runtime, "config", None), "workflow", None),
        "orchestration",
        None,
    )
    return str(
        getattr(orchestration, "oa_profile", "") or ""
    ).strip() or LEGACY_ORCHESTRATOR_ROLE


def configured_orchestrator_role_from_config(config: Any) -> Any | None:
    """Resolve the semantic OA role when only config is available.

    Run Manager's bounded semantic review is a config-only projection. It must
    use the same explicit profile/instance binding as transport and checkpoint
    operations, and fail closed when an explicit profile is missing.
    """

    orchestration = getattr(
        getattr(config, "workflow", None), "orchestration", None
    )
    profile = str(getattr(orchestration, "oa_profile", "") or "").strip()
    profile = profile or LEGACY_ORCHESTRATOR_ROLE
    roles = getattr(config, "roles", ()) or ()
    for role in roles:
        if str(getattr(role, "name", "") or "") == profile:
            return role
    if profile != LEGACY_ORCHESTRATOR_ROLE:
        for role in roles:
            if str(getattr(role, "instance_id", "") or "") == profile:
                return role
    return None


def configured_orchestrator_role(runtime: Any) -> Any | None:
    """Return the configured OA role, with the 194 fallback.

    A profile is first treated as a role name and then as an instance id.  An
    explicit but missing profile is fail-closed; it never silently falls back
    to the legacy role.
    """

    profile = orchestrator_profile_name(runtime)
    finder = getattr(runtime, "_find_role_by_name", None)
    if callable(finder):
        role = finder(profile)
        if role is not None:
            return role
    if profile != LEGACY_ORCHESTRATOR_ROLE:
        finder = getattr(runtime, "_find_role_by_instance", None)
        if callable(finder):
            return finder(profile)
    return configured_orchestrator_role_from_config(runtime.config)


def orchestrator_instance_id(runtime: Any) -> str:
    role = configured_orchestrator_role(runtime)
    if role is not None:
        return str(
            getattr(role, "instance_id", "")
            or getattr(role, "name", "")
            or orchestrator_profile_name(runtime)
        )
    return orchestrator_profile_name(runtime)


__all__ = [
    "LEGACY_ORCHESTRATOR_ROLE",
    "configured_orchestrator_role",
    "configured_orchestrator_role_from_config",
    "orchestrator_instance_id",
    "orchestrator_profile_name",
]
