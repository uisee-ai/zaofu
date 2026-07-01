"""Memory store with active+archive layout.

Layout:

    .zf/memory/shared.md       ← today's active shared memory
    .zf/memory/<role>.md       ← today's active role memory
    .zf/memory/shared/         ← archived shared memory by date
      2026-04-10.md
      2026-04-12.md
    .zf/memory/<role>/         ← archived role memory by date
      2026-04-13.md

Rotation: `add()` checks the active file's mtime; if not today,
the file is moved into the archive dir via rotate_if_needed() from
zf.core.state.rotation. First write of a new day creates a fresh
active file. Existing pre-archive `.zf/memory/shared.md` is treated
as "today" (or "some recent day") and rotates lazily on next write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from zf.core.state.locks import locked_path
from zf.core.state.rotation import list_archives, rotate_if_needed


_MEMORY_TYPES = {"decision", "pattern", "fix", "context"}
_DECAY_DAYS = {"decision": 30, "pattern": 60, "fix": 7, "context": 14}


@dataclass
class MemoryEntry:
    type: str
    content: str
    added_at: str = ""
    max_days: int = 30


class MemoryStore:
    """Read/write memory files in .zf/memory/ using active+archive layout."""

    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir

    # -- path helpers --

    def _active_file(self, role: str | None) -> Path:
        filename = "shared.md" if role is None else f"{role}.md"
        return self.memory_dir / filename

    def _archive_dir(self, role: str | None) -> Path:
        dirname = "shared" if role is None else role
        return self.memory_dir / dirname

    # -- public API --

    def add(self, role: str | None, mem_type: str, content: str) -> MemoryEntry:
        """Append a memory entry to today's active file, rotating if needed."""
        if mem_type not in _MEMORY_TYPES:
            raise ValueError(
                f"Invalid memory type: {mem_type}. Must be one of {_MEMORY_TYPES}"
            )

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        active = self._active_file(role)
        archive_dir = self._archive_dir(role)

        now = datetime.now(timezone.utc).isoformat()
        max_days = _DECAY_DAYS.get(mem_type, 30)
        entry = MemoryEntry(
            type=mem_type, content=content, added_at=now, max_days=max_days
        )

        header = f"<!-- type: {mem_type}; max_days: {max_days}; last_updated: {now} -->"
        block = f"\n{header}\n## {content.split(chr(10))[0][:80]}\n{content}\n"

        # B-MEM-02: serialize the rotate+append critical section across
        # processes. rotate_if_needed() is a non-atomic read-check-move;
        # without a lock, concurrent add() from multiple panes near the UTC
        # day boundary loses blocks and raises FileNotFoundError when two
        # processes race the rotate rename (smoke evidence:
        # docs/records/2026-06-16-axisB-code-debt-smoke-REPORT.md). The lock
        # file (active + ".lock") is separate from the active file, so it
        # survives the rotation rename.
        with locked_path(active):
            rotate_if_needed(active, archive_dir)
            with active.open("a", encoding="utf-8") as f:
                f.write(block)

        return entry

    def get(
        self,
        role: str | None,
        *,
        since: str | None = None,
        until: str | None = None,
        last_days: int | None = None,
    ) -> list[MemoryEntry]:
        """Read memory entries.

        By default returns today's active file + all archives.
        last_days=N: today (active) + last N-1 archived days.
        since/until: inclusive YYYY-MM-DD bounds on archived filenames.

        Active file is always included in the result (it's "today" by
        definition, and must not be filtered out by date range).
        """
        entries: list[MemoryEntry] = []
        # 1. Archives first (chronological order)
        archive_dir = self._archive_dir(role)
        archive_files = list_archives(
            archive_dir,
            since=since,
            until=until,
            last_days=(last_days - 1) if last_days and last_days > 1 else None,
            suffix=".md",
        )
        # last_days=1 means "today only" → skip all archives
        if last_days == 1:
            archive_files = []
        for f in archive_files:
            entries.extend(self._parse_entries(f.read_text(encoding="utf-8")))

        # 2. Today's active file
        active = self._active_file(role)
        if active.exists():
            entries.extend(self._parse_entries(active.read_text(encoding="utf-8")))

        return entries

    # -- parsing --

    def _parse_entries(self, text: str) -> list[MemoryEntry]:
        """Parse memory entries from markdown with metadata comments."""
        entries: list[MemoryEntry] = []
        pattern = re.compile(
            r"<!-- type: (\w+); max_days: (\d+); last_updated: ([^>]+) -->\n"
            r"## ([^\n]*)\n(.*?)(?=\n<!-- type:|\Z)",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            entries.append(
                MemoryEntry(
                    type=match.group(1),
                    content=match.group(5).strip(),
                    added_at=match.group(3).strip(),
                    max_days=int(match.group(2)),
                )
            )
        return entries
