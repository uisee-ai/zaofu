"""Tests for `zf watch` — structured event tail (D1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator", task_id="T1"))
    log.append(ZfEvent(type="agent.tool.use", actor="dev", task_id="T1",
                       payload={"tool": "Read", "input": {"path": "src/x.py"}}))
    log.append(ZfEvent(type="agent.text", actor="dev", task_id="T1",
                       payload={"text": "I will read the file first"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
    log.append(ZfEvent(type="agent.tool.use", actor="review", task_id="T1",
                       payload={"tool": "Bash", "input": {"command": "pytest"}}))
    return tmp_path


def test_watch_lists_recent_events(project: Path, capsys):
    result = main(["watch", "--last", "10"])
    assert result == 0
    out = capsys.readouterr().out
    assert "task.dispatched" in out
    assert "agent.tool.use" in out
    assert "agent.text" in out
    assert "dev.build.done" in out


def test_watch_uses_project_state_dir(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n',
        encoding="utf-8",
    )
    state_dir = tmp_path / "runtime-state"
    state_dir.mkdir()
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-RUNTIME")
    )

    result = main(["watch", "--last", "10"])

    assert result == 0
    out = capsys.readouterr().out
    assert "TASK-RUNTIME" in out
    assert not (tmp_path / ".zf").exists()


def test_watch_filters_by_role(project: Path, capsys):
    result = main(["watch", "--role", "dev", "--last", "20"])
    assert result == 0
    out = capsys.readouterr().out
    # dev events appear
    assert "agent.text" in out
    # review events do not
    assert "review" not in out or "actor=`dev`" in out  # no review actor
    # Specifically: orchestrator and review actors should be filtered out
    lines = [l for l in out.splitlines() if "actor=" in l or "actor:" in l]
    for line in lines:
        if "agent." in line or "dev." in line:
            assert "dev" in line


def test_watch_filters_by_type(project: Path, capsys):
    result = main(["watch", "--type", "agent.tool.use", "--last", "20"])
    assert result == 0
    out = capsys.readouterr().out
    assert "agent.tool.use" in out
    assert "agent.text" not in out
    assert "dev.build.done" not in out


def test_watch_renders_tool_call_with_tool_name(project: Path, capsys):
    result = main(["watch", "--type", "agent.tool.use", "--last", "20"])
    out = capsys.readouterr().out
    assert "Read" in out  # tool name from payload
    assert "Bash" in out


def test_watch_handles_missing_events_file(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    result = main(["watch", "--last", "10"])
    # Should not crash; should exit cleanly
    assert result == 0


def test_watch_help_listed_in_main(capsys):
    """`zf --help` should mention watch as a subcommand."""
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "watch" in out
