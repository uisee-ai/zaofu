"""Read-only readiness gate for the final same-source acceptance review."""

from __future__ import annotations

from typing import Any, Mapping


def evaluate_unified_acceptance(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Return readiness only; never authorize release or turn missing into pass."""
    if audit.get("verdict") != "pass":
        raise ValueError("unified acceptance requires an audit pass")
    if audit.get("gate_exit") != 0:
        raise ValueError("unified acceptance gate is not green")
    if audit.get("same_revision_verified") is not True:
        raise ValueError("unified acceptance requires one verified source revision")
    if audit.get("source_variant_count") != 1:
        raise ValueError("unified acceptance requires one source variant")
    requirements = audit.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("unified acceptance requires explicit requirements")
    bad = [
        row.get("id", "<unknown>")
        for row in requirements
        if row.get("status") != "pass" or row.get("source_verified") is not True
    ]
    if bad:
        raise ValueError(f"unified acceptance has non-passing requirements: {','.join(bad)}")
    return {
        "status": "ready_for_operator_review",
        "requirement_count": len(requirements),
        "same_revision_verified": True,
        "release_authorized": False,
        "scope": "readiness only; final human acceptance and release authorization remain separate",
    }
