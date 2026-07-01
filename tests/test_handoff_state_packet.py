"""ZF-LH-SP-002 — `zf handoff --format state-packet` tests (doc 39 §4.1)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from zf.cli.handoff import run


def test_state_packet_format_in_choices() -> None:
    """Argparse registers state-packet as a valid --format value."""
    import argparse

    from zf.cli.handoff import register

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    ns = parser.parse_args(["handoff", "--format", "state-packet"])
    assert ns.format == "state-packet"


def test_register_adds_task_argument() -> None:
    import argparse

    from zf.cli.handoff import register

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    ns = parser.parse_args(["handoff", "--task", "TASK-X"])
    assert ns.task_id == "TASK-X"


def test_md_format_still_works(tmp_path: Path, capsys, monkeypatch) -> None:
    """v1 contract preserved — `zf handoff` without --format
    still produces the markdown summary."""
    _bootstrap_project(tmp_path, monkeypatch)
    rc = run(Namespace(format="md", task_id=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Handoff Summary" in out
    assert "### Completed" in out


def test_json_format_still_works(tmp_path: Path, capsys, monkeypatch) -> None:
    _bootstrap_project(tmp_path, monkeypatch)
    rc = run(Namespace(format="json", task_id=None))
    out = capsys.readouterr().out
    import json

    data = json.loads(out)
    assert rc == 0
    assert "done" in data
    assert "in_progress" in data
    assert "backlog" in data


def test_state_packet_format_outputs_markdown(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    _bootstrap_project(tmp_path, monkeypatch)
    rc = run(Namespace(format="state-packet", task_id=None))
    out = capsys.readouterr().out
    assert rc == 0
    # State Packet markdown render has these landmarks
    assert "# State Packet" in out
    assert "projection only, not runtime truth" in out


def test_state_packet_explicit_task_id(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """--task TASK-X routes to that task's projection."""
    _bootstrap_project(tmp_path, monkeypatch, with_task="TASK-EXPLICIT")
    rc = run(Namespace(format="state-packet", task_id="TASK-EXPLICIT"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "TASK-EXPLICIT" in out


# ---------------------------------------------------------------------------
# Fixture helper — minimal zaofu state_dir so `run` doesn't crash
# ---------------------------------------------------------------------------


def _bootstrap_project(
    tmp_path: Path,
    monkeypatch,
    *,
    with_task: str | None = None,
) -> None:
    """Set up the minimum on-disk layout to satisfy run()."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    # Minimal zf.yaml so resolve_project_context() finds a config
    (tmp_path / "zf.yaml").write_text(
        "project:\n  name: test\nroles: []\n",
    )
    # Minimal kanban.json (TaskStore expects a JSON list at the top level)
    kanban = state_dir / "kanban.json"
    if with_task:
        import json

        kanban.write_text(json.dumps([
            {
                "id": with_task,
                "title": "explicit task",
                "status": "in_progress",
                "active_dispatch_id": "disp-1",
                "assigned_to": "dev-1",
                "contract": {
                    "behavior": "do something",
                    "feature_id": "",
                },
            },
        ]))
    else:
        kanban.write_text("[]")
    # Empty events.jsonl
    (state_dir / "events.jsonl").write_text("")
    # cd into the temp project so resolve_project_context picks it up
    monkeypatch.chdir(tmp_path)
