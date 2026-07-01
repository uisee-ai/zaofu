"""Tests for memory active+archive layout (G-MEM-4)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.memory.store import MemoryStore


def _set_mtime(path: Path, days_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def _day_str(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestMemoryActiveArchive:
    def test_add_writes_to_shared_active_file(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        store.add(None, "decision", "use bcrypt for passwords")
        active = tmp_path / "memory" / "shared.md"
        assert active.exists()
        assert "bcrypt" in active.read_text()

    def test_add_writes_to_role_active_file(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        store.add("dev", "pattern", "use composition over inheritance")
        active = tmp_path / "memory" / "dev.md"
        assert active.exists()
        assert "composition" in active.read_text()
        # shared.md is untouched
        assert not (tmp_path / "memory" / "shared.md").exists()

    def test_same_day_adds_do_not_rotate(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        store.add("dev", "decision", "A")
        store.add("dev", "decision", "B")
        active = tmp_path / "memory" / "dev.md"
        content = active.read_text()
        assert "A" in content
        assert "B" in content
        # No archive dir
        assert not (tmp_path / "memory" / "dev").exists()

    def test_cross_day_add_rotates_legacy_file_to_archive(self, tmp_path: Path):
        """A legacy shared.md from several days ago gets rotated on first add."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)
        legacy = memory_dir / "dev.md"
        legacy.write_text("<!-- type: decision; max_days: 30; last_updated: old -->\n## old\nold content\n")
        _set_mtime(legacy, days_ago=3)

        store = MemoryStore(memory_dir)
        store.add("dev", "decision", "fresh content")

        # legacy file rotated to archive
        assert (memory_dir / "dev" / f"{_day_str(3)}.md").exists()
        # new active file contains only fresh content
        active = memory_dir / "dev.md"
        assert active.exists()
        assert "fresh content" in active.read_text()
        assert "old content" not in active.read_text()

    def test_get_merges_active_and_archive(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        store = MemoryStore(memory_dir)
        # Simulate an archive (manually create)
        archive_dir = memory_dir / "dev"
        archive_dir.mkdir(parents=True)
        old = archive_dir / f"{_day_str(2)}.md"
        old.write_text(
            "<!-- type: decision; max_days: 30; last_updated: old -->\n"
            "## old decision\nold content\n"
        )
        # And today's active
        store.add("dev", "pattern", "new pattern")

        entries = store.get("dev")
        contents = [e.content for e in entries]
        assert any("old content" in c for c in contents)
        assert any("new pattern" in c for c in contents)

    def test_get_last_days_filter(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        store = MemoryStore(memory_dir)
        # Set up archives at days 1, 3, 10
        archive_dir = memory_dir / "dev"
        archive_dir.mkdir(parents=True)
        for days_ago in (10, 3, 1):
            (archive_dir / f"{_day_str(days_ago)}.md").write_text(
                f"<!-- type: context; max_days: 14; last_updated: x -->\n"
                f"## entry from {days_ago} days ago\n"
                f"content-{days_ago}\n"
            )
        store.add("dev", "decision", "today entry")

        # last_days=3 → today + 2 days back = today + day-1 + day-2
        entries = store.get("dev", last_days=3)
        contents = " ".join(e.content for e in entries)
        assert "today entry" in contents
        assert "content-1" in contents
        assert "content-3" not in contents  # day 3 is outside window
        assert "content-10" not in contents

    def test_get_since_until(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        store = MemoryStore(memory_dir)
        archive_dir = memory_dir / "dev"
        archive_dir.mkdir(parents=True)
        for days_ago in (10, 5, 2):
            (archive_dir / f"{_day_str(days_ago)}.md").write_text(
                f"<!-- type: context; max_days: 14; last_updated: x -->\n"
                f"## entry-{days_ago}\n"
                f"content-{days_ago}\n"
            )

        entries = store.get("dev", since=_day_str(6), until=_day_str(1))
        contents = " ".join(e.content for e in entries)
        assert "content-2" in contents
        assert "content-5" in contents
        assert "content-10" not in contents

    def test_get_no_filter_returns_everything(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        store = MemoryStore(memory_dir)
        archive_dir = memory_dir / "dev"
        archive_dir.mkdir(parents=True)
        (archive_dir / f"{_day_str(5)}.md").write_text(
            "<!-- type: decision; max_days: 30; last_updated: x -->\n## e\narchived\n"
        )
        store.add("dev", "pattern", "active")

        entries = store.get("dev")  # no filter
        assert len(entries) >= 2

    def test_existing_shared_md_is_read_as_today(self, tmp_path: Path):
        """A pre-existing shared.md (from legacy pre-archive code) should be
        readable by MemoryStore without migration."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)
        legacy = memory_dir / "shared.md"
        legacy.write_text(
            "<!-- type: decision; max_days: 30; last_updated: now -->\n"
            "## legacy\nlegacy content\n"
        )
        # mtime is today by default
        store = MemoryStore(memory_dir)
        entries = store.get(None)
        assert any("legacy content" in e.content for e in entries)

    def test_rotation_collision_appends(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)
        # Pre-existing archive for yesterday
        archive_dir = memory_dir / "dev"
        archive_dir.mkdir()
        yesterday_archive = archive_dir / f"{_day_str(1)}.md"
        yesterday_archive.write_text("old archive\n")
        # Active file with mtime from yesterday
        active = memory_dir / "dev.md"
        active.write_text("yesterday active\n")
        _set_mtime(active, days_ago=1)

        # Triggering rotation via add() → should concat into existing archive
        store = MemoryStore(memory_dir)
        store.add("dev", "decision", "today fresh")

        combined = yesterday_archive.read_text()
        assert "old archive" in combined
        assert "yesterday active" in combined
        assert "today fresh" not in combined  # that's in new active file
