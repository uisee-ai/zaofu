"""Production safety checks for the Feishu Issue/Triage surface.

The project ``feishu.yaml`` may opt into signed actions while schema defaults
remain backward compatible for local fixtures and older deployments.
Real-provider startup must use the stricter posture before it can project new
mutation cards.  This module is intentionally read-only: it reports
configuration defects and never changes control-plane state or secrets.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


def production_config_issues(
    config: Any,
    *,
    environ: Mapping[str, str] | None = None,
    require_enabled: bool = False,
    require_callback_signature: bool = False,
) -> tuple[str, ...]:
    """Return actionable production errors for an enabled Feishu identity.

    Disabled Feishu integrations remain valid in local/CI environments.  A
    real transport caller should refuse to start when any returned issue is
    present; mock transports may continue to support compatibility fixtures.
    ``environ`` is injectable so the check is deterministic in tests.
    """
    integrations = getattr(config, "integrations", None)
    identity = getattr(integrations, "feishu_identity", None)
    if identity is None or not bool(getattr(identity, "enabled", False)):
        return (
            ("integrations.feishu_identity.enabled must be true for real transport",)
            if require_enabled else ()
        )
    env = os.environ if environ is None else environ
    issues: list[str] = []
    if not bool(getattr(identity, "require_signed_actions", False)):
        issues.append("integrations.feishu_identity.require_signed_actions must be true")
    action_env = str(
        getattr(identity, "action_token_secret_env", "ZF_FEISHU_ACTION_TOKEN_SECRET")
        or "ZF_FEISHU_ACTION_TOKEN_SECRET"
    )
    if not str(env.get(action_env, "") or ""):
        issues.append(f"Feishu action-token secret is missing: {action_env}")
    if require_callback_signature:
        signature_key_env = str(
            getattr(
                identity,
                "verification_token_env",
                "ZF_FEISHU_VERIFICATION_TOKEN",
            )
            or "ZF_FEISHU_VERIFICATION_TOKEN"
        )
        if not str(env.get(signature_key_env, "") or ""):
            issues.append(
                "Feishu webhook signature Encrypt Key is missing: "
                f"{signature_key_env}"
            )
    users = getattr(identity, "users", {}) or {}
    if not users:
        issues.append("integrations.feishu_identity.users must map real open_id principals")
    else:
        placeholders = sorted(
            str(principal) for principal in users
            if "__zf_" in str(principal).lower() or "__feishu_" in str(principal).lower()
        )
        if placeholders:
            issues.append("Feishu identity contains unresolved placeholder open_id values")
    routing = getattr(integrations, "feishu_routing", {}) or {}
    if not isinstance(routing, dict) or not routing:
        issues.append("integrations.feishu_routing must bind at least one real project chat")
    elif any(
        "__zf_" in str(key).lower() or "__feishu_" in str(key).lower()
        for key in routing
    ):
        issues.append("Feishu routing contains unresolved placeholder chat values")
    elif not any(
        not (
            str(key).strip() == "*"
            or str(key).strip().startswith(("*#", "*@"))
            or str(key).strip().endswith(":*")
        )
        for key in routing
    ):
        issues.append("Feishu routing must include an exact project chat for write actions")
    return tuple(issues)


def production_context_issues(
    contexts: object,
    *,
    environ: Mapping[str, str] | None = None,
    require_enabled: bool = False,
    require_callback_signature: bool = False,
) -> tuple[str, ...]:
    """Check every project context before a shared real Feishu WS starts."""
    issues: list[str] = []
    seen: set[str] = set()
    for context in contexts if isinstance(contexts, (list, tuple, set)) else (contexts,):
        for issue in production_config_issues(
            getattr(context, "config", None),
            environ=environ,
            require_enabled=require_enabled,
            require_callback_signature=require_callback_signature,
        ):
            if issue not in seen:
                seen.add(issue)
                issues.append(issue)
    return tuple(issues)


def bridge_production_context_issues(
    context: object | None,
    inbound_resolver: object | None,
    routed_routes: object,
    app_id: str,
) -> tuple[str, ...]:
    """Resolve bridge project contexts and apply the strict real-provider gate."""
    contexts = []
    if inbound_resolver is None:
        if context is not None:
            contexts.append(context)
    else:
        for route in routed_routes if isinstance(routed_routes, list) else ():
            resolved = inbound_resolver.resolve(
                app_id=route.app_id,
                chat_id=route.chat_id,
            )
            if resolved is not None:
                contexts.append(resolved.context)
    if not contexts:
        return ("no project context available for Feishu bridge preflight",)
    return production_context_issues(contexts, require_enabled=True)


__all__ = [
    "bridge_production_context_issues",
    "production_config_issues",
    "production_context_issues",
]
