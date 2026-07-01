"""Tests for kanban / cost CLI archive-aware flags (G-TASK-1 / G-COST-1 tail).

Sprint validation criteria:
  - `zf kanban` (default) — active tasks only; archived done tasks NOT shown
  - `zf kanban --all` — active + every archived task ever
  - `zf kanban --days N` — active + last N days of archive
  - `zf cost --days N` — totals for the last N days only
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.cost.tracker import CostTracker
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    return tmp_path


class TestKanbanDefaultActiveOnly:
    def test_default_board_hides_archived_done(self, project: Path, capsys):
        store = TaskStore(project / ".zf" / "kanban.json")
        store.add(Task(id="T-LIVE", title="active task"))
        store.add(Task(id="T-OLD", title="finished task"))
        store.update("T-OLD", status="done")  # → archived

        rc = main(["kanban"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "T-LIVE" in out
        assert "T-OLD" not in out

    def test_all_flag_shows_archived(self, project: Path, capsys):
        store = TaskStore(project / ".zf" / "kanban.json")
        store.add(Task(id="T-LIVE", title="active task"))
        store.add(Task(id="T-OLD", title="finished task"))
        store.update("T-OLD", status="done")

        rc = main(["kanban", "--all"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "T-LIVE" in out
        assert "T-OLD" in out

    def test_days_flag_includes_recent_archive(self, project: Path, capsys):
        store = TaskStore(project / ".zf" / "kanban.json")
        store.add(Task(id="T-LIVE", title="active task"))
        store.add(Task(id="T-OLD", title="finished task"))
        store.update("T-OLD", status="done")  # archived today

        rc = main(["kanban", "--days", "1"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "T-OLD" in out


class TestCostDaysFlag:
    def test_days_flag_passes_through(self, project: Path, capsys):
        tracker = CostTracker(project / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", input_tokens=100, output_tokens=50)

        rc = main(["cost", "--days", "7"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dev" in out

    def test_days_flag_argparse_accepts_int(self, project: Path, capsys):
        # No data; just verify argparse + CLI plumbing accept --days N
        rc = main(["cost", "--days", "30"])
        assert rc == 0
