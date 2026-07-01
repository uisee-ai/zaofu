"""Memory staleness detection — structural signals only.

Age-based decay is handled at the storage layer via the active+archive
date layout: callers pass ``last_days=N`` to ``MemoryStore.get`` to get
only fresh entries. StalenessChecker focuses on structural signals like
referenced file paths that no longer exist on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zf.core.memory.store import MemoryEntry


@dataclass
class StaleEntry:
    entry: MemoryEntry
    reason: str  # path_missing, symbol_missing


class StalenessChecker:
    """Check memory entries for structural staleness (missing paths)."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def check(self, entries: list[MemoryEntry]) -> list[StaleEntry]:
        stale: list[StaleEntry] = []
        for entry in entries:
            for word in entry.content.split():
                if "/" in word and not word.startswith("http"):
                    clean = word.strip("`\"'(),;:")
                    path = self.workspace / clean
                    if clean and not path.exists() and len(clean) > 2:
                        stale.append(StaleEntry(entry=entry, reason="path_missing"))
                        break
        return stale
