"""Tests for zf trace show."""

from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def test_trace_show_outputs_correlation_trace(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    user = writer.append(ZfEvent(type="user.message", actor="human"))
    writer.append(ZfEvent(type="task.created", task_id="T1", causation_id=user.id))

    result = main(["trace", "show", user.correlation_id or ""])

    assert result == 0
    out = capsys.readouterr().out
    assert "user.message" in out
    assert "task.created" in out
    assert user.correlation_id in out


def test_trace_show_json_can_use_event_id(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    user = writer.append(ZfEvent(type="user.message", actor="human"))
    created = writer.append(ZfEvent(
        type="task.created",
        task_id="T1",
        causation_id=user.id,
    ))

    result = main(["trace", "show", created.id, "--format", "json"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "correlation"
    assert [event["type"] for event in data["events"]] == [
        "user.message",
        "task.created",
    ]


def test_trace_show_unknown_exits_nonzero(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()

    result = main(["trace", "show", "trace-missing"])

    assert result != 0


def test_trace_export_otlp_json_stdout_and_file(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="build api",
        status="in_progress",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.started",
        id="evt-fanout",
        payload={
            "feature_id": "F-1",
            "fanout_id": "FX",
            "stage_id": "impl",
            "expected_children": [{"child_id": "dev", "task_id": "T1"}],
        },
    ))
    log.append(ZfEvent(
        type="fanout.child.completed",
        id="evt-child",
        task_id="T1",
        payload={
            "feature_id": "F-1",
            "fanout_id": "FX",
            "child_id": "dev",
            "task_id": "T1",
        },
    ))
    canonical_before = {
        path.name: path.read_bytes()
        for path in (state_dir / "events.jsonl", state_dir / "kanban.json")
    }

    result = main(["trace", "export", "F-1", "--format", "otlp-json", "--state-dir", str(state_dir)])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    spans = data["resource_spans"][0]["scope_spans"][0]["spans"]
    assert spans
    assert spans[0]["attributes"]["zaofu.target_id"] == "F-1"

    output = tmp_path / "otlp.json"
    result = main([
        "trace", "export", "--target", "F-1", "--format", "otlp-json",
        "--output", str(output), "--state-dir", str(state_dir),
    ])

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["resource_spans"]
    assert {
        path.name: path.read_bytes()
        for path in (state_dir / "events.jsonl", state_dir / "kanban.json")
    } == canonical_before
