"""Tests for `zf state reconcile` — kanban ⇄ tmux desync detection.

Backlog: 2026-05-14 P2 #10.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zf.cli import state as state_cli
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _zf_yaml(state_dir_name: str = ".zf", tmux: str = "zf-test") -> str:
    return (
        f"project:\n  name: t\n  state_dir: {state_dir_name}\n"
        f"session:\n  tmux_session: {tmux}\n"
        "roles:\n"
        "- name: arch\n  backend: claude-code\n  permission_mode: bypass\n"
        "- name: dev\n  backend: claude-code\n  permission_mode: bypass\n"
        "  replicas: 4\n"
    )


def _args(state_dir: Path, reset: bool = False, dry_run: bool = False):
    return argparse.Namespace(
        state_dir=str(state_dir),
        reset=reset,
        dry_run=dry_run,
    )


def _seed_kanban(state_dir: Path, tasks: list[tuple[str, str, str | None]]):
    ts = TaskStore(state_dir / "kanban.json")
    for tid, status, assignee in tasks:
        ts.add(Task(id=tid, title=tid, status=status, assigned_to=assignee))
    return ts


def test_reconcile_reports_orphans_when_no_tmux(tmp_path, monkeypatch, capsys):
    (tmp_path / "zf.yaml").write_text(_zf_yaml(), encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    _seed_kanban(state_dir, [
        ("T1", "in_progress", "dev-1"),
        ("T2", "in_progress", "arch"),
        ("T3", "done", None),  # terminal — not in-flight
    ])
    # Force "no tmux session" by making tmux command fail
    monkeypatch.setattr(
        state_cli, "_live_tmux_panes", lambda config: set()
    )

    rc = state_cli._run_reconcile(_args(state_dir))
    assert rc == 2  # state inconsistent, no action
    out = capsys.readouterr().out
    assert "orphaned: 2" in out
    assert "T1" in out and "T2" in out
    assert "T3" not in out  # terminal task not surfaced


def test_reconcile_healthy_when_assignee_matches_live_pane(
    tmp_path, monkeypatch, capsys,
):
    (tmp_path / "zf.yaml").write_text(_zf_yaml(), encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    _seed_kanban(state_dir, [
        ("T1", "in_progress", "dev-1"),
    ])
    monkeypatch.setattr(
        state_cli, "_live_tmux_panes", lambda config: {"dev-1", "arch"}
    )

    rc = state_cli._run_reconcile(_args(state_dir))
    assert rc == 0
    out = capsys.readouterr().out
    assert "healthy:  1" in out
    assert "orphaned: 0" in out


def test_reconcile_reset_pushes_back_to_ready(tmp_path, monkeypatch, capsys):
    (tmp_path / "zf.yaml").write_text(_zf_yaml(), encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    _seed_kanban(state_dir, [
        ("T1", "in_progress", "dev-1"),
        ("T2", "dispatched", "arch"),
    ])
    monkeypatch.setattr(state_cli, "_live_tmux_panes", lambda config: set())

    rc = state_cli._run_reconcile(_args(state_dir, reset=True))
    assert rc == 0
    ts = TaskStore(state_dir / "kanban.json")
    t1 = ts.get("T1")
    assert t1.status == "ready"
    assert t1.assigned_to is None
    t2 = ts.get("T2")
    assert t2.status == "ready"
    assert t2.assigned_to is None

    # Emitted a status_changed event per task
    events = [
        json.loads(ln)
        for ln in (state_dir / "events.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    types = [e["type"] for e in events]
    assert types.count("task.status_changed") == 2
    for e in events:
        if e["type"] == "task.status_changed":
            assert e["payload"]["source"] == "state_reconcile"
            assert e["payload"]["to_status"] == "ready"


def test_reconcile_dry_run_does_not_reset(tmp_path, monkeypatch, capsys):
    (tmp_path / "zf.yaml").write_text(_zf_yaml(), encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    _seed_kanban(state_dir, [
        ("T1", "in_progress", "dev-1"),
    ])
    monkeypatch.setattr(state_cli, "_live_tmux_panes", lambda config: set())

    rc = state_cli._run_reconcile(_args(state_dir, reset=True, dry_run=True))
    assert rc == 2
    # task remains in_progress
    t1 = TaskStore(state_dir / "kanban.json").get("T1")
    assert t1.status == "in_progress"
    assert t1.assigned_to == "dev-1"
    # no events emitted
    assert not (state_dir / "events.jsonl").exists()


def test_reconcile_empty_kanban(tmp_path, monkeypatch, capsys):
    (tmp_path / "zf.yaml").write_text(_zf_yaml(), encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    # No tasks seeded
    monkeypatch.setattr(state_cli, "_live_tmux_panes", lambda config: set())

    rc = state_cli._run_reconcile(_args(state_dir))
    assert rc == 0
    assert "nothing to reconcile" in capsys.readouterr().out


def test_reconcile_orphan_without_assignee(tmp_path, monkeypatch, capsys):
    """A task can be in_progress without an assignee (legacy / bug)."""
    (tmp_path / "zf.yaml").write_text(_zf_yaml(), encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    _seed_kanban(state_dir, [
        ("T1", "in_progress", None),
    ])
    monkeypatch.setattr(state_cli, "_live_tmux_panes", lambda config: set())

    rc = state_cli._run_reconcile(_args(state_dir))
    assert rc == 2
    assert "T1" in capsys.readouterr().out


def test_live_tmux_panes_no_tmux_session_returns_empty():
    """When config has no session config, return empty set."""
    class _NoSession:
        roles = []
        session = None
    # No session attr -> default to empty
    assert state_cli._live_tmux_panes(_NoSession()) == set()


def test_live_tmux_panes_uses_instance_id_not_name(monkeypatch):
    """RoleConfig has both ``name`` and ``instance_id``; we must match the latter."""

    class _Role:
        def __init__(self, name, instance_id):
            self.name = name
            self.instance_id = instance_id

    class _Session:
        tmux_session = "zf-test"

    class _Config:
        roles = [
            _Role("dev", "dev-1"),
            _Role("dev", "dev-2"),
            _Role("arch", "arch"),
        ]
        session = _Session()

    fake_stdout = (
        "dev-1\tsome title\t/wd/.zf/workdirs/dev-1/project\n"
        "dev-2\tsome other\t/wd/.zf/workdirs/dev-2/project\n"
        "arch\ttitle\t/wd/.zf/workdirs/arch/project\n"
        "\tunrelated\t/elsewhere\n"
    )

    class _Result:
        returncode = 0
        stdout = fake_stdout

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Result())
    live = state_cli._live_tmux_panes(_Config())
    assert live == {"dev-1", "dev-2", "arch"}
