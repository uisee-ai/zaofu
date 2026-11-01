"""Exact compatibility bindings for already admitted Task Pipeline policies."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping


def bind_disabled_risk_limit_opt_out(
    policy: Mapping[str, Any], *, admitted_digest: str,
) -> dict[str, Any] | None:
    """Bind an inactive default-to-zero edit without changing admitted identity.

    Only the historical 180s/1-turn/$1 defaults may become all-zero while
    review stays disabled. Reconstructing the complete old digest proves
    every other policy field is unchanged; active gates cannot use this path.
    """
    historical = deepcopy(dict(policy))
    configured_digest = str(historical.pop("profile_digest", ""))
    admission = historical.get("integration_admission") or {}
    risk = admission.get("risk_review") or {}
    if admission.get("default") != "verify_admitted" or risk.get("enabled") is not False:
        return None
    fields = ("timeout_seconds", "max_turns", "budget_usd")
    if any(risk.get(field) != 0 for field in fields):
        return None
    risk.update(timeout_seconds=180, max_turns=1, budget_usd=1.0)
    digest = hashlib.sha256(json.dumps(
        historical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    if digest != admitted_digest:
        return None
    bound = deepcopy(dict(policy))
    bound["profile_digest"] = admitted_digest
    bound["policy_binding_resolution"] = {
        "kind": "disabled_risk_default_limit_opt_out",
        "admitted_profile_digest": admitted_digest,
        "configured_profile_digest": configured_digest,
    }
    return bound


def bind_legacy_scoped_pool(
    config: Any, policy: Mapping[str, Any], *, flow_kind: str, admitted_digest: str,
) -> dict[str, Any] | None:
    """Accept only the exact historical policy after reversing role scoping.

    The old digest remains the admitted identity.  Scoped names are a
    deterministic binding of the old pool references to existing Flow roles;
    no capacity, lifecycle, gate, or other policy change is accepted.
    """
    prefix = f"{flow_kind}-"
    if flow_kind not in {"issue", "prd", "refactor"}:
        return None
    roles = {
        str(role.instance_id): role for role in getattr(config, "roles", ())
        if str(getattr(role, "flow_kind", "") or "") == flow_kind
    }
    legacy = deepcopy(dict(policy))
    configured_digest = str(legacy.pop("profile_digest", ""))
    pools = legacy.get("pools")
    if not isinstance(pools, dict):
        return None
    changed = False
    for stage, pool in pools.items():
        if not isinstance(pool, dict):
            return None
        refs = pool.get("role_instances")
        if not isinstance(refs, list):
            return None
        unscoped = []
        for role in refs:
            if not isinstance(role, str) or not role.startswith(prefix) or role not in roles:
                return None
            expected_kind = "writer" if stage == "impl" else "reader"
            if str(getattr(roles[role], "role_kind", "") or "") != expected_kind:
                return None
            unscoped.append(role.removeprefix(prefix))
            changed = True
        if len(unscoped) != len(set(unscoped)):
            return None
        pool["role_instances"] = unscoped
        for profile in pool.get("worker_profiles", []) or []:
            role = profile.get("role") if isinstance(profile, dict) else None
            if not isinstance(role, str) or not role.startswith(prefix) or role not in roles:
                return None
            profile["role"] = role.removeprefix(prefix)
    digest = hashlib.sha256(json.dumps(
        legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    if not changed or digest != admitted_digest:
        return None
    bound = deepcopy(dict(policy))
    bound["profile_digest"] = admitted_digest
    bound["role_binding_resolution"] = {
        "kind": "legacy_multi_flow_namespace",
        "admitted_profile_digest": admitted_digest,
        "configured_profile_digest": configured_digest,
    }
    return bound
