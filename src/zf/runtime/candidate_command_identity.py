"""Bind frozen Candidate command IDs to their verification obligations."""

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def conflicting_tiers_message(source_id: str, first: str, second: str) -> str:
    return (
        f"Candidate command ID {source_id[:128]!r} has conflicting verification tiers "
        f"{first[:64]!r} and {second[:64]!r} for the same command. Give independent obligations "
        "distinct stable command IDs, or correct their tier if they describe one obligation. "
        "Preserve required checks and matrix bindings; do not change shell spelling to bypass the check."
    )


def candidate_command_ids(
    commands: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    """Preserve legacy IDs for unambiguous literals and scope collisions to their tier."""
    literals: dict[str, str] = {}
    source_tiers: dict[tuple[str, str], str] = {}
    tiers_by_digest: dict[str, set[str]] = defaultdict(set)
    for row in commands:
        digest = str(row["command_digest"])
        literal = str(row["command"])
        if literals.setdefault(digest, literal) != literal:
            raise ValueError("candidate verification command digest collision")
        source_id = str(row.get("command_id") or row.get("id") or "")
        tier = str(row.get("tier") or "integration")
        previous = source_tiers.setdefault((digest, source_id), tier)
        if previous != tier:
            raise ValueError(conflicting_tiers_message(source_id, previous, tier))
        tiers_by_digest[digest].add(tier)
    identities = {}
    for digest, tiers in tiers_by_digest.items():
        for tier in tiers:
            command_id = "candidate-cmd-" + digest[:20]
            if len(tiers) > 1:
                command_id += "-" + hashlib.sha256(tier.encode()).hexdigest()[:12]
            identities[digest, tier] = command_id
    if len(set(identities.values())) != len(identities):
        raise ValueError("candidate verification command ID collision")
    return identities
