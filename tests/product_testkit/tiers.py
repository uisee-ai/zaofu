"""CI tier selection for product-test manifests."""

from __future__ import annotations

from .manifest import ProductManifest

TIER_ORDER = {"unit": "T0", "integration": "T1", "e2e": "T2", "security": "T3", "perf": "T3", "release": "T3"}


def ci_plan(manifest: ProductManifest) -> dict[str, tuple[str, ...]]:
    """Return stable command IDs grouped by minimum CI tier."""
    result: dict[str, list[str]] = {f"T{i}": [] for i in range(4)}
    for item in manifest.test_units:
        tier = TIER_ORDER[str(item["tier"])]
        result[tier].append(str(item["id"]))
    return {key: tuple(value) for key, value in result.items()}


def commands_for_ci_tier(manifest: ProductManifest, tier: str) -> tuple[str, ...]:
    """Return commands required up to a CI tier, preserving manifest order."""
    if tier not in {"T0", "T1", "T2", "T3"}:
        raise ValueError(f"unknown CI tier: {tier}")
    rank = int(tier[1:])
    plan = ci_plan(manifest)
    return tuple(command for index in range(rank + 1) for command in plan[f"T{index}"])

