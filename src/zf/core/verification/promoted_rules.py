"""Promoted rules — review feedback persisted as permanent gates."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from zf.core.verification.gates import CommandGate


@dataclass
class PromotedRule:
    category: str
    rule: str  # shell command
    fix_hint: str
    promoted_at: str
    occurrences: int = 1


class PromotedRulesStore:
    """Manage .zf/promoted_rules.jsonl."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, category: str, rule: str, fix_hint: str = "") -> PromotedRule:
        """Add a promoted rule."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = PromotedRule(
            category=category,
            rule=rule,
            fix_hint=fix_hint,
            promoted_at=str(time.time()),
        )
        with self.path.open("a") as f:
            f.write(json.dumps({
                "category": entry.category,
                "rule": entry.rule,
                "fix_hint": entry.fix_hint,
                "promoted_at": entry.promoted_at,
                "occurrences": entry.occurrences,
            }) + "\n")
        return entry

    def list(self) -> list[PromotedRule]:
        """List all promoted rules."""
        if not self.path.exists():
            return []
        rules: list[PromotedRule] = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                rules.append(PromotedRule(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        return rules

    def to_gates(self) -> list[CommandGate]:
        """Convert promoted rules to CommandGate instances."""
        return [
            CommandGate(name=f"promoted:{r.category}", command=r.rule)
            for r in self.list()
        ]
