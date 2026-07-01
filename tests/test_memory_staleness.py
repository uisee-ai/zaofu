"""Tests for memory staleness detection."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from zf.core.memory.store import MemoryEntry
from zf.core.memory.staleness import StalenessChecker


class TestStalenessChecker:
    def test_fresh_entry_not_stale(self, tmp_path: Path):
        checker = StalenessChecker(tmp_path)
        entry = MemoryEntry(
            type="decision",
            content="Use JWT",
            added_at=datetime.now(timezone.utc).isoformat(),
            max_days=30,
        )
        stale = checker.check([entry])
        assert len(stale) == 0

    def test_old_entry_not_age_flagged(self, tmp_path: Path):
        """Age-based decay is now handled at the storage layer via
        active+archive date filtering (G-MEM-4). StalenessChecker should
        only check structural signals like missing paths — age decay
        belongs to MemoryStore.get(last_days=N)."""
        checker = StalenessChecker(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        entry = MemoryEntry(
            type="decision",
            content="Old but still valid decision",
            added_at=old.isoformat(),
            max_days=30,
        )
        stale = checker.check([entry])
        assert stale == []
        assert not any(s.reason == "age_expired" for s in stale)

    def test_path_missing(self, tmp_path: Path):
        checker = StalenessChecker(tmp_path)
        entry = MemoryEntry(
            type="pattern",
            content="See src/nonexistent/file.py for details",
            added_at=datetime.now(timezone.utc).isoformat(),
            max_days=60,
        )
        stale = checker.check([entry])
        assert any(s.reason == "path_missing" for s in stale)

    def test_path_exists_not_stale(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "real.py").write_text("# real")
        checker = StalenessChecker(tmp_path)
        entry = MemoryEntry(
            type="pattern",
            content="See src/real.py for details",
            added_at=datetime.now(timezone.utc).isoformat(),
            max_days=60,
        )
        stale = checker.check([entry])
        assert len(stale) == 0
