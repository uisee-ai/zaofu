"""LH-6.T1/T5/T6 skeleton tests — run the loop 3 iterations with a
mock scenario runner, verify results.tsv + report generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskEvidence
from zf.core.task.store import TaskStore

from tests.longhorizon.loop import LoopConfig, run_loop
from tests.longhorizon.results_log import read_recent
from tests.longhorizon.report import render


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    # Fake project with a git repo so preflight passes.
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]\n")
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    # init a throwaway git repo
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"],
                    cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _mock_runner(state_dir: Path, scenario: str) -> None:
    """Pretend to run a scenario by writing a done task + events."""
    store = TaskStore(state_dir / "kanban.json")
    task_id = f"T{len(store.list_all_with_archive()) + 1}"
    store.add(Task(id=task_id, title=f"mock {scenario}",
                    evidence=TaskEvidence(commit="abc")))
    store.update(task_id, status="done")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="task.created", actor="zf-cli", task_id=task_id,
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="judge.passed", actor="judge", task_id=task_id,
    ))


class TestLoopSkeleton:
    def test_three_iterations_write_three_rows(self, project):
        results = project / "results.tsv"
        cfg = LoopConfig(
            scenario="S1", iterations=3,
            state_dir=project / ".zf",
            results_path=results, dry_run=True,
        )
        n = run_loop(cfg, runner=_mock_runner)
        assert n == 3
        rows = read_recent(results, n=10)
        assert len(rows) == 3
        # First iteration is baseline, subsequent are keep/discard.
        statuses = [r["note"].split(":", 1)[0] for r in rows]
        assert "baseline" in statuses

    def test_guard_fail_is_captured(self, project):
        """If a guard script fails, guard_status column is 'fail:<name>'."""
        # Make a guard fail: create a rogue truth store
        (project / ".zf" / "state.json").write_text("{}")
        results = project / "results.tsv"
        cfg = LoopConfig(
            scenario="S1", iterations=1,
            state_dir=project / ".zf",
            results_path=results, dry_run=True,
        )
        run_loop(cfg, runner=_mock_runner)
        rows = read_recent(results, n=10)
        assert rows[0]["guard_status"].startswith("fail:")


class TestReportGenerator:
    def test_renders_sections_from_results(self, tmp_path):
        from tests.longhorizon.results_log import append_row, ResultRow
        results = tmp_path / "results.tsv"
        append_row(results, ResultRow(
            iteration=0, commit="a1", vcr=0.70, mtts=5.0,
            cost_per_task=0.10, rework_ratio=0.5,
            guard_status="pass", note="baseline",
        ))
        append_row(results, ResultRow(
            iteration=1, commit="a2", vcr=0.80, mtts=6.0,
            cost_per_task=0.09, rework_ratio=0.3,
            guard_status="pass", note="keep: max_rework_attempts=5",
        ))
        append_row(results, ResultRow(
            iteration=2, commit="a3", vcr=0.50, mtts=4.0,
            cost_per_task=0.15, rework_ratio=0.8,
            guard_status="pass", note="discard: recycle_threshold=0.6",
        ))
        text = render(results)
        assert "Executive summary" in text
        assert "Top" in text
        assert "baseline" in text.lower()
        assert "keep" in text
        assert "discard" in text

    def test_empty_results_gives_placeholder(self, tmp_path):
        assert "(no results" in render(tmp_path / "nonexistent.tsv")
