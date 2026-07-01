"""Tests for zf cost CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.cost.tracker import CostTracker


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])
    return tmp_path


class TestCostCLI:
    def test_no_data(self, project_dir: Path, capsys):
        result = main(["cost"])
        assert result == 0
        captured = capsys.readouterr()
        assert "no cost" in captured.out.lower()

    def test_with_data(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 10000, 5000)
        result = main(["cost"])
        assert result == 0
        captured = capsys.readouterr()
        assert "dev" in captured.out
        assert "$" in captured.out

    def test_budget_within(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500)
        result = main(["cost", "--budget", "100"])
        assert result == 0
        captured = capsys.readouterr()
        assert "WITHIN" in captured.out

    def test_budget_exceeded(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 1_000_000, 1_000_000, "default")
        result = main(["cost", "--budget", "0.001"])
        assert result == 0
        captured = capsys.readouterr()
        assert "EXCEEDED" in captured.out

    def test_doctor_reports_projection_health(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500, usage_sample_id="sample-1")

        result = main(["cost", "--doctor"])

        assert result == 0
        captured = capsys.readouterr()
        assert "Cost Projection Doctor" in captured.out
        assert "duplicate_entries: 0" in captured.out
