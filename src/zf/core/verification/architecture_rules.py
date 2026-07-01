"""Architecture rules parser — ARCHITECTURE_RULES.md → auto-generated gates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from zf.core.verification.gates import CommandGate


@dataclass
class ArchitectureRule:
    name: str
    check: str  # shell command
    fix: str
    why: str


def parse_rules(path: Path) -> list[ArchitectureRule]:
    """Parse architecture rules from a markdown file."""
    if not path.exists():
        return []

    text = path.read_text()
    rules: list[ArchitectureRule] = []
    pattern = re.compile(
        r"## Rule:\s*(.+?)\n"
        r"(?:.*?- check:\s*`(.+?)`.*?)?"
        r"(?:.*?- fix:\s*\"(.+?)\".*?)?"
        r"(?:.*?- why:\s*\"(.+?)\".*?)?",
        re.DOTALL,
    )

    for match in pattern.finditer(text):
        name = match.group(1).strip()
        check = (match.group(2) or "").strip()
        fix = (match.group(3) or "").strip()
        why = (match.group(4) or "").strip()
        if name and check:
            rules.append(ArchitectureRule(name=name, check=check, fix=fix, why=why))

    return rules


def rules_to_gates(rules: list[ArchitectureRule]) -> list[CommandGate]:
    """Convert architecture rules to CommandGate instances."""
    return [
        CommandGate(name=f"arch:{rule.name}", command=rule.check)
        for rule in rules
    ]
