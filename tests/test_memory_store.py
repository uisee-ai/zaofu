"""Tests for memory store."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.memory.store import MemoryStore


class TestMemoryStore:
    def test_add_shared(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        entry = store.add(None, "decision", "Use JWT for auth")
        assert entry.type == "decision"
        assert (tmp_path / "memory" / "shared.md").exists()

    def test_add_role(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        store.add("dev", "pattern", "Always run tests before commit")
        assert (tmp_path / "memory" / "dev.md").exists()

    def test_get_empty(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        assert store.get(None) == []

    def test_get_after_add(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        store.add(None, "decision", "Use JWT for auth")
        entries = store.get(None)
        assert len(entries) == 1
        assert entries[0].type == "decision"
        assert "JWT" in entries[0].content

    def test_multiple_entries(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        store.add(None, "decision", "Use JWT")
        store.add(None, "pattern", "Run tests first")
        entries = store.get(None)
        assert len(entries) == 2

    def test_invalid_type_raises(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        with pytest.raises(ValueError, match="Invalid memory type"):
            store.add(None, "invalid", "content")

    def test_entry_has_timestamp(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        entry = store.add(None, "fix", "Fix import order")
        assert entry.added_at != ""

    def test_entry_has_max_days(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "memory")
        entry = store.add(None, "fix", "Quick fix")
        assert entry.max_days == 7  # fix type decays in 7 days
