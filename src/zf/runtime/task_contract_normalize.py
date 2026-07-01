"""Runtime normalization for task-map supplied task contracts.

Layer 1 accepts task maps produced by different skills and agents. Those
producers often use workflow words such as ``judge`` or ``contract`` while the
canonical task contract only accepts the kernel verification tiers. Normalize at
the runtime boundary so scheduling and preflight see one contract shape.
"""

from __future__ import annotations

from typing import Any

from zf.core.task.schema import VALID_VERIFICATION_TIERS


_TIER_ALIASES = {
    "acceptance": "manual_evidence",
    "build": "static",
    "check": "runtime",
    "contract": "runtime",
    "contracts": "runtime",
    "integration": "runtime",
    "judge": "manual_evidence",
    "lint": "static",
    "live_smoke": "e2e",
    "live_smoke_optional": "e2e",
    "parity": "runtime",
    "review": "manual_evidence",
    "smoke": "e2e",
    "test": "runtime",
    "tests": "runtime",
    "typecheck": "static",
    "unit": "runtime",
    "verify": "runtime",
}

_GENERIC_OWNER_ROLES = {"", "dev", "impl", "writer", "coding", "coding-agent"}


def canonical_verification_tiers(
    value: Any,
    *,
    verification: str = "",
    validation: dict[str, Any] | None = None,
) -> list[str]:
    """Return canonical verification tiers, preserving first-seen order."""
    tiers: list[str] = []
    for item in _string_list(value):
        normalized = _TIER_ALIASES.get(item.lower(), item.lower())
        if normalized in VALID_VERIFICATION_TIERS and normalized not in tiers:
            tiers.append(normalized)
    if tiers:
        return tiers
    validation = validation if isinstance(validation, dict) else {}
    if verification.strip() or str(validation.get("command") or "").strip():
        return ["runtime"]
    return []


def owner_fields_from_task_map_item(raw: dict[str, Any]) -> tuple[str, str]:
    """Derive ``(owner_role, owner_instance)`` from a task-map item.

    Refactor/lane task maps often emit ``owner_role: dev`` plus
    ``preferred_impl_role: dev-lane-0``. In ZaoFu config, ``dev`` is usually a
    role family, not a concrete role name. The scheduler can validate and route
    this only when the lane role is recorded as ``owner_instance``.
    """
    owner_role = str(raw.get("owner_role") or "").strip()
    owner_instance = _first_nonempty(
        raw.get("owner_instance"),
        raw.get("assigned_to"),
        raw.get("preferred_impl_role"),
    )
    if not owner_instance and _looks_like_role_instance(owner_role):
        owner_instance = owner_role
        owner_role = ""
    if owner_instance and owner_role.lower() in _GENERIC_OWNER_ROLES:
        owner_role = ""
    if not owner_role and not owner_instance:
        owner_role = "dev"
    return owner_role, owner_instance


def _looks_like_role_instance(value: str) -> bool:
    text = value.strip()
    return bool(text and ("-lane-" in text or text.count("-") >= 2))


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []

