"""Tests for cost.jsonl active+archive layout (G-COST-1)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker


def _set_mtime(path: Path, days_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def _day_str(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestCostActiveArchive:
    def test_record_creates_active_file(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500, "sonnet")
        assert (tmp_path / "cost.jsonl").exists()
        assert not (tmp_path / "cost").exists()

    def test_same_day_records_do_not_rotate(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500, "sonnet")
        tracker.record_usage("dev", 2000, 800, "sonnet")
        assert not (tmp_path / "cost").exists()
        totals = tracker.per_role_totals()
        assert totals["dev"].entries == 2

    def test_cross_day_rotates_to_archive(self, tmp_path: Path):
        path = tmp_path / "cost.jsonl"
        path.write_text(
            '{"role":"dev","input_tokens":100,"output_tokens":50,"model":"default","cost_usd":0.001,"ts":0.0}\n'
        )
        _set_mtime(path, days_ago=2)

        tracker = CostTracker(path)
        tracker.record_usage("dev", 500, 200, "sonnet")

        assert (tmp_path / "cost" / f"{_day_str(2)}.jsonl").exists()
        active_lines = [l for l in path.read_text().splitlines() if l.strip()]
        assert len(active_lines) == 1  # only new record
        entry = json.loads(active_lines[0])
        assert entry["input_tokens"] == 500

    def test_per_role_totals_merges_archives(self, tmp_path: Path):
        path = tmp_path / "cost.jsonl"
        archive_dir = tmp_path / "cost"
        archive_dir.mkdir()
        (archive_dir / f"{_day_str(2)}.jsonl").write_text(
            '{"role":"dev","input_tokens":100,"output_tokens":50,"model":"default","cost_usd":0.1,"ts":0.0}\n'
        )
        tracker = CostTracker(path)
        tracker.record_usage("dev", 200, 100, "default")

        totals = tracker.per_role_totals()
        dev_sum = totals["dev"]
        # input: 100 (archive) + 200 (active) = 300
        assert dev_sum.input_tokens == 300
        assert dev_sum.entries == 2

    def test_per_role_totals_last_days(self, tmp_path: Path):
        path = tmp_path / "cost.jsonl"
        archive_dir = tmp_path / "cost"
        archive_dir.mkdir()
        for days_ago in (10, 5, 2, 1):
            (archive_dir / f"{_day_str(days_ago)}.jsonl").write_text(
                f'{{"role":"dev","input_tokens":{days_ago * 100},"output_tokens":0,"model":"default","cost_usd":0.0,"ts":0.0}}\n'
            )
        tracker = CostTracker(path)
        tracker.record_usage("dev", 999, 0, "default")

        # last_days=3 = today + 2 archive days back = today + day1 + day2
        # input should be: 999 (today) + 100 (day1) + 200 (day2) = 1299
        totals = tracker.per_role_totals(last_days=3)
        assert totals["dev"].input_tokens == 1299

    def test_daily_totals_groups_by_date(self, tmp_path: Path):
        """New daily_totals() method groups entries by YYYY-MM-DD."""
        path = tmp_path / "cost.jsonl"
        archive_dir = tmp_path / "cost"
        archive_dir.mkdir()
        (archive_dir / f"{_day_str(2)}.jsonl").write_text(
            f'{{"role":"dev","input_tokens":100,"output_tokens":50,"model":"default","cost_usd":0.1,"ts":0.0}}\n'
        )
        tracker = CostTracker(path)
        tracker.record_usage("dev", 50, 25, "default")

        daily = tracker.daily_totals()
        assert _day_str(2) in daily
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today in daily
