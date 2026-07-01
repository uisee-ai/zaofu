"""Tests for cost tracker."""

from __future__ import annotations

from pathlib import Path

from zf.core.cost.tracker import CostTracker


class TestCostTracker:
    def test_record_usage_returns_cost(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        cost = tracker.record_usage("dev", 1000, 500, "default")
        assert cost > 0

    def test_record_persists(self, tmp_path: Path):
        path = tmp_path / "cost.jsonl"
        tracker = CostTracker(path)
        tracker.record_usage("dev", 1000, 500)
        assert path.exists()
        assert len(path.read_text().strip().split("\n")) == 1

    def test_per_role_totals(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500)
        tracker.record_usage("dev", 2000, 1000)
        tracker.record_usage("review", 500, 200)

        totals = tracker.per_role_totals()
        assert "dev" in totals
        assert "review" in totals
        assert totals["dev"].input_tokens == 3000
        assert totals["dev"].entries == 2
        assert totals["review"].entries == 1

    def test_total_usd(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", 1_000_000, 0, "default")  # $3.00
        total = tracker.total_usd()
        assert abs(total - 3.0) < 0.01

    def test_check_budget_within(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500)
        assert tracker.check_budget(100.0) is True

    def test_check_budget_exceeded(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", 1_000_000, 1_000_000, "default")
        assert tracker.check_budget(0.001) is False

    def test_empty_cost_file(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        assert tracker.total_usd() == 0.0
        assert tracker.per_role_totals() == {}
        assert tracker.check_budget(100.0) is True

    def test_usage_sample_id_is_idempotent(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")

        first = tracker.record_usage(
            "dev", 1000, 500, usage_sample_id="sample-1"
        )
        second = tracker.record_usage(
            "dev", 1000, 500, usage_sample_id="sample-1"
        )

        totals = tracker.per_role_totals()
        assert first > 0
        assert second == 0.0
        assert totals["dev"].entries == 1
        assert totals["dev"].input_tokens == 1000

    def test_source_event_id_is_idempotent_without_sample(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")

        tracker.record_usage("dev", 1000, 500, source_event_id="evt-1")
        tracker.record_usage("dev", 1000, 500, source_event_id="evt-1")

        totals = tracker.per_role_totals()
        assert totals["dev"].entries == 1

    def test_duplicate_report_surfaces_legacy_suspects(self, tmp_path: Path):
        path = tmp_path / "cost.jsonl"
        path.write_text(
            "\n".join([
                '{"role":"dev","instance_id":"dev","input_tokens":100,'
                '"output_tokens":50,"model":"default","backend":"codex",'
                '"cost_source":"rate","cost_usd":0.001,"ts":1.0}',
                '{"role":"dev","instance_id":"dev","input_tokens":100,'
                '"output_tokens":50,"model":"default","backend":"codex",'
                '"cost_source":"rate","cost_usd":0.001,"ts":2.0}',
            ])
            + "\n"
        )

        report = CostTracker(path).duplicate_report()

        assert report["missing_dedupe_key"] == 2
        assert report["suspect_legacy_duplicate_entries"] == 1
