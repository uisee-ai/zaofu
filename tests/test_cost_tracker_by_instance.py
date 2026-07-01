"""Tests for G-INST-6: cost tracker supports instance_id dimension.

Backward compat: per_role_totals() still aggregates by role string
(which is now whatever the caller passes — instance_id). A new
per_instance_totals() is a semantic alias for callers that want to
make the intent explicit. record_usage gains an optional instance_id
kwarg that, when provided, takes precedence over role for the entry
key. Default behavior unchanged for single-instance configs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker


class TestInstanceRecordUsage:
    def test_record_usage_with_instance_id(self, tmp_path: Path):
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage(
            "dev", input_tokens=100, output_tokens=50,
            instance_id="dev-1",
        )
        tracker.record_usage(
            "dev", input_tokens=200, output_tokens=75,
            instance_id="dev-2",
        )
        totals = tracker.per_instance_totals()
        assert "dev-1" in totals
        assert "dev-2" in totals
        assert totals["dev-1"].input_tokens == 100
        assert totals["dev-2"].input_tokens == 200

    def test_per_role_totals_aggregates_across_instances(self, tmp_path: Path):
        """Legacy per_role_totals must still work, aggregating dev-1 + dev-2
        under "dev"."""
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", 100, 50, instance_id="dev-1")
        tracker.record_usage("dev", 200, 75, instance_id="dev-2")

        role_totals = tracker.per_role_totals()
        assert "dev" in role_totals
        assert role_totals["dev"].input_tokens == 300  # 100 + 200
        assert role_totals["dev"].entries == 2

    def test_record_usage_without_instance_id_backward_compat(
        self, tmp_path: Path
    ):
        """Legacy call without instance_id: role string is the key for
        both per_role and per_instance views."""
        tracker = CostTracker(tmp_path / "cost.jsonl")
        tracker.record_usage("dev", input_tokens=100, output_tokens=50)

        role_totals = tracker.per_role_totals()
        inst_totals = tracker.per_instance_totals()
        assert role_totals["dev"].input_tokens == 100
        assert inst_totals["dev"].input_tokens == 100


class TestCliCostByInstanceFlag:
    def test_cost_cli_by_instance_flag_parses(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".zf").mkdir()
        tracker = CostTracker(tmp_path / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 100, 50, instance_id="dev-1")
        tracker.record_usage("dev", 200, 75, instance_id="dev-2")

        from zf.cli.main import main
        rc = main(["cost", "--by-instance"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dev-1" in out
        assert "dev-2" in out
