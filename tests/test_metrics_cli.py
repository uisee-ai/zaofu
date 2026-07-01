"""LH-1.T2: `zf metrics snapshot` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskEvidence
from zf.core.task.store import TaskStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]\n")
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestCli:
    def test_snapshot_empty_project_exits_zero(self, project, capsys):
        rc = main(["metrics", "snapshot", "--format", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        # 12 primary metrics all present
        for key in ("mtts", "vcr", "throughput_per_hour", "rework_ratio",
                    "cost_per_task", "budget_breach_rate"):
            assert key in data

    def test_snapshot_table_format(self, project, capsys):
        rc = main(["metrics", "snapshot", "--format", "table"])
        assert rc == 0
        out = capsys.readouterr().out
        for group in ("A. 持续性", "B. 对齐", "C. 进度", "D. 经济"):
            assert group in out

    def test_snapshot_diff(self, project, capsys, tmp_path):
        baseline = tmp_path / "baseline.json"
        main(["metrics", "snapshot", "--format", "json"])
        out = capsys.readouterr().out
        baseline.write_text(out)

        # Add a done task to change throughput & vcr
        store = TaskStore(project / ".zf" / "kanban.json")
        store.add(Task(id="T1", title="a",
                       evidence=TaskEvidence(commit="abc")))
        store.update("T1", status="done")

        capsys.readouterr()  # clear
        rc = main(["metrics", "snapshot", "--diff", str(baseline)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "delta" in out
        assert "VCR" in out

    def test_snapshot_uses_project_state_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        state = tmp_path / "runtime-state"
        state.mkdir()
        (state / "kanban.json").write_text("[]\n")
        EventLog(state / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        (tmp_path / "zf.yaml").write_text(
            'version: "1.0"\n'
            "project:\n"
            "  name: metrics-state-dir\n"
            "  state_dir: runtime-state\n",
            encoding="utf-8",
        )

        rc = main(["metrics", "snapshot", "--format", "json"])

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["events_considered"] == 1

    def test_snapshot_help_lists_metrics(self, capsys):
        with pytest.raises(SystemExit):
            main(["metrics", "--help"])
        out = capsys.readouterr().out
        assert "snapshot" in out

    def test_top_level_help_includes_metrics(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "metrics" in out
