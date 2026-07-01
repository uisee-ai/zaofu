"""Tests for shared lazy rotation helper (G-ROT-0)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.state.rotation import list_archives, rotate_if_needed


def _set_mtime(path: Path, days_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def _day_str(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestRotateIfNeeded:
    def test_noop_when_active_missing(self, tmp_path: Path):
        active = tmp_path / "shared.md"
        assert rotate_if_needed(active) is False
        assert not active.exists()

    def test_noop_when_mtime_is_today(self, tmp_path: Path):
        active = tmp_path / "shared.md"
        active.write_text("today content")
        rotated = rotate_if_needed(active)
        assert rotated is False
        assert active.exists()
        assert active.read_text() == "today content"

    def test_rotate_when_mtime_is_yesterday(self, tmp_path: Path):
        active = tmp_path / "shared.md"
        active.write_text("yesterday content")
        _set_mtime(active, days_ago=1)

        rotated = rotate_if_needed(active)
        assert rotated is True
        assert not active.exists()  # moved
        archive_dir = tmp_path / "shared"
        archived = archive_dir / f"{_day_str(1)}.md"
        assert archived.exists()
        assert archived.read_text() == "yesterday content"

    def test_rotate_uses_explicit_archive_dir(self, tmp_path: Path):
        active = tmp_path / "events.jsonl"
        active.write_text('{"type":"x"}\n')
        _set_mtime(active, days_ago=2)

        custom_dir = tmp_path / "my-archive"
        assert rotate_if_needed(active, archive_dir=custom_dir) is True
        assert not active.exists()
        assert (custom_dir / f"{_day_str(2)}.jsonl").exists()

    def test_jsonl_suffix_preserved(self, tmp_path: Path):
        active = tmp_path / "events.jsonl"
        active.write_text('{"a":1}\n')
        _set_mtime(active, days_ago=1)
        rotate_if_needed(active)
        assert (tmp_path / "events" / f"{_day_str(1)}.jsonl").exists()

    def test_json_suffix_preserved(self, tmp_path: Path):
        active = tmp_path / "kanban.json"
        active.write_text("[]")
        _set_mtime(active, days_ago=1)
        rotate_if_needed(active)
        assert (tmp_path / "kanban" / f"{_day_str(1)}.json").exists()

    def test_rotate_collision_appends(self, tmp_path: Path):
        # Pre-existing archive for the same day
        archive_dir = tmp_path / "shared"
        archive_dir.mkdir()
        existing = archive_dir / f"{_day_str(1)}.md"
        existing.write_text("already-archived\n")

        active = tmp_path / "shared.md"
        active.write_text("new-content\n")
        _set_mtime(active, days_ago=1)

        rotated = rotate_if_needed(active)
        assert rotated is True
        assert not active.exists()
        assert existing.exists()
        content = existing.read_text()
        assert "already-archived" in content
        assert "new-content" in content

    def test_now_date_override(self, tmp_path: Path):
        """Allow caller to fix 'today' for deterministic tests."""
        active = tmp_path / "shared.md"
        active.write_text("x")
        # Real mtime is today, but we tell rotate that today is tomorrow
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        rotated = rotate_if_needed(active, now_date=tomorrow)
        assert rotated is True
        # Archive named by active's real mtime day (today)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert (tmp_path / "shared" / f"{today}.md").exists()


class TestListArchives:
    def _make_archive(self, dir: Path, days_ago: int, suffix: str = ".md") -> Path:
        dir.mkdir(parents=True, exist_ok=True)
        path = dir / f"{_day_str(days_ago)}{suffix}"
        path.write_text(f"content-{days_ago}")
        return path

    def test_empty_dir(self, tmp_path: Path):
        assert list_archives(tmp_path / "missing") == []
        (tmp_path / "empty").mkdir()
        assert list_archives(tmp_path / "empty") == []

    def test_sorted_oldest_first(self, tmp_path: Path):
        d = tmp_path / "archive"
        self._make_archive(d, days_ago=5)
        self._make_archive(d, days_ago=1)
        self._make_archive(d, days_ago=3)
        files = list_archives(d)
        assert [f.stem for f in files] == [
            _day_str(5), _day_str(3), _day_str(1),
        ]

    def test_last_days_filter(self, tmp_path: Path):
        """last_days=K = up to K archive days back from today.

        Archive stems must be in [today - K, today - 1]. Since today is
        not usually in archive, yields at most K archives (1 per day).
        """
        d = tmp_path / "archive"
        self._make_archive(d, days_ago=10)
        self._make_archive(d, days_ago=5)
        self._make_archive(d, days_ago=3)
        self._make_archive(d, days_ago=1)
        # last_days=3 means "archives whose stem is in [today-3, today-1]"
        # → day1 and day3 (day5/day10 are outside)
        files = list_archives(d, last_days=3)
        stems = [f.stem for f in files]
        assert _day_str(1) in stems
        assert _day_str(3) in stems
        assert _day_str(5) not in stems
        assert _day_str(10) not in stems

    def test_since_filter(self, tmp_path: Path):
        d = tmp_path / "archive"
        self._make_archive(d, days_ago=10)
        self._make_archive(d, days_ago=3)
        files = list_archives(d, since=_day_str(5))
        stems = [f.stem for f in files]
        assert _day_str(3) in stems
        assert _day_str(10) not in stems

    def test_until_filter(self, tmp_path: Path):
        d = tmp_path / "archive"
        self._make_archive(d, days_ago=10)
        self._make_archive(d, days_ago=1)
        files = list_archives(d, until=_day_str(5))
        stems = [f.stem for f in files]
        assert _day_str(10) in stems
        assert _day_str(1) not in stems

    def test_suffix_filter(self, tmp_path: Path):
        d = tmp_path / "archive"
        self._make_archive(d, days_ago=1, suffix=".md")
        self._make_archive(d, days_ago=1, suffix=".jsonl")
        md_only = list_archives(d, suffix=".md")
        jsonl_only = list_archives(d, suffix=".jsonl")
        assert len(md_only) == 1 and md_only[0].suffix == ".md"
        assert len(jsonl_only) == 1 and jsonl_only[0].suffix == ".jsonl"
