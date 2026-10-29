"""Deterministic owner contract for the normal workflow frontier."""

from __future__ import annotations

from typing import Any


NORMAL_FRONTIER_OWNERS = frozenset({"run_manager", "wrc"})


def normal_frontier_owner(config: Any) -> str:
    orchestration = getattr(
        getattr(getattr(config, "workflow", None), "orchestration", None),
        "normal_frontier_owner",
        "run_manager",
    )
    owner = str(orchestration or "run_manager").strip().lower()
    return owner if owner in NORMAL_FRONTIER_OWNERS else "run_manager"


def run_manager_may_consume_normal_frontier(config: Any) -> bool:
    return normal_frontier_owner(config) == "run_manager"


__all__ = [
    "NORMAL_FRONTIER_OWNERS",
    "normal_frontier_owner",
    "run_manager_may_consume_normal_frontier",
]
