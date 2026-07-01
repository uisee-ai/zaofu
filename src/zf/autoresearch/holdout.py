"""Holdout scenario registry helpers for autoresearch."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HoldoutScenario:
    id: str
    purpose: str = ""
    command: str = ""
    expected: str = ""
    visibility: str = "holdout"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_holdout_registry(path: Path) -> list[HoldoutScenario]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    raw_items = data.get("scenarios") if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        return []
    scenarios: list[HoldoutScenario] = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        scenarios.append(HoldoutScenario(
            id=str(item.get("id")),
            purpose=str(item.get("purpose") or ""),
            command=str(item.get("command") or ""),
            expected=str(item.get("expected") or ""),
            visibility=str(item.get("visibility") or "holdout"),
            evidence=[str(v) for v in item.get("evidence") or []],
        ))
    return scenarios


def discover_holdout_registry(project_root: Path) -> Path | None:
    for rel in (
        "tests/fixtures/holdout/registry.json",
        "docs/impl/evals/holdout-registry.json",
    ):
        path = Path(project_root) / rel
        if path.exists():
            return path
    return None


def holdout_projection(project_root: Path) -> dict[str, Any]:
    registry = discover_holdout_registry(project_root)
    scenarios = load_holdout_registry(registry) if registry is not None else []
    return {
        "registry_path": str(registry) if registry is not None else "",
        "scenario_count": len(scenarios),
        "scenarios": [scenario.to_dict() for scenario in scenarios],
        "gate": "available" if scenarios else "skipped",
    }


__all__ = [
    "HoldoutScenario",
    "load_holdout_registry",
    "discover_holdout_registry",
    "holdout_projection",
]
