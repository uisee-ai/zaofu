"""CLI tests for run archive commands."""

from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def test_archive_run_cli_uses_project_context_and_rebuilds_projection(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = tmp_path / "repo"
    project.mkdir()
    state_dir = tmp_path / "runtime-state"
    state_dir.mkdir()
    live = tmp_path / "live" / ".zf"
    live.mkdir(parents=True)
    EventLog(live / "events.jsonl").append(
        ZfEvent(type="test.passed", actor="test-1", task_id="TASK-CLI")
    )
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-CLI", title="CLI archive validation", status="in_progress")
    )
    (project / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-test\n"
        "  state_dir: ../runtime-state\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    result = main([
        "archive-run",
        "--run-id",
        "RUN-CLI",
        "--trace-id",
        "trace-cli",
        "--test-task-id",
        "TASK-CLI",
        "--scenario-id",
        "scripted",
        "--status",
        "passed",
        "--live-state-dir",
        str(live),
    ])

    captured = capsys.readouterr()
    assert result == 0
    assert "RUN-CLI" in captured.out
    assert (state_dir / "runs" / "RUN-CLI" / "artifact_manifest.json").exists()
    index = json.loads((state_dir / "runs" / "index.json").read_text(encoding="utf-8"))
    assert index["runs"][0]["run_id"] == "RUN-CLI"
    task = TaskStore(state_dir / "kanban.json").get("TASK-CLI")
    assert task is not None
    assert task.status == "done"


def test_runs_reconcile_cli_archives_stale_run(tmp_path: Path, monkeypatch):
    project = tmp_path / "repo"
    state_dir = project / ".zf"
    live = tmp_path / "live" / ".zf"
    state_dir.mkdir(parents=True)
    live.mkdir(parents=True)
    (project / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: cli-test\n',
        encoding="utf-8",
    )
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(
            type="run.started",
            actor="zf-cli",
            task_id="TASK-STALE",
            payload={"run_id": "RUN-STALE", "live_state_dir": str(live)},
            ts="2026-05-06T00:00:00+00:00",
        )
    )
    monkeypatch.chdir(project)

    result = main(["runs", "reconcile", "--stale-after", "0"])

    assert result == 0
    assert (state_dir / "runs" / "RUN-STALE" / "artifact_manifest.json").exists()
