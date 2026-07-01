"""Tests for G-INST-9: `zf kanban assign` accepts instance_id + legacy migration.

Two concerns:
  1. `zf kanban assign T1 dev-2` must validate that dev-2 exists in
     zf.yaml before writing task.assigned_to.
  2. When loading a legacy kanban.json where tasks have
     assigned_to="dev" but the config now has instance_ids dev-1/dev-2,
     the orchestrator must migrate on startup to pick the first
     matching instance (dev-1) so dispatch doesn't silently fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


class TestKanbanAssignCliValidation:
    def test_assign_accepts_known_instance_id(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".zf").mkdir()
        # Write a zf.yaml with dev replicas=2
        (tmp_path / "zf.yaml").write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "session: {tmux_session: t}\n"
            "roles:\n"
            "  - {name: dev, replicas: 2, backend: mock}\n"
        )
        store = TaskStore(tmp_path / ".zf" / "kanban.json")
        store.add(Task(id="T1", title="x"))

        from zf.cli.main import main
        rc = main(["kanban", "assign", "T1", "dev-2"])
        assert rc == 0
        assert store.get("T1").assigned_to == "dev-2"

    def test_assign_rejects_unknown_instance_id(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".zf").mkdir()
        (tmp_path / "zf.yaml").write_text(
            "version: '1.0'\n"
            "project: {name: t}\n"
            "session: {tmux_session: t}\n"
            "roles:\n"
            "  - {name: dev, replicas: 2, backend: mock}\n"
        )
        store = TaskStore(tmp_path / ".zf" / "kanban.json")
        store.add(Task(id="T1", title="x"))

        from zf.cli.main import main
        rc = main(["kanban", "assign", "T1", "dev-99"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "dev-99" in err
        # Task should remain unassigned
        assert (store.get("T1").assigned_to or "") == ""


class TestLegacyAssignedToMigration:
    def test_legacy_assigned_to_migrated_to_instance_1(
        self, state_dir: Path
    ):
        """Old kanban.json has task.assigned_to='dev' but the current
        config expanded dev into dev-1/dev-2. Orchestrator init must
        rewrite assigned_to='dev' → 'dev-1' so dispatch still works."""
        store = TaskStore(state_dir / "kanban.json")
        # Legacy entry: raw role name as assigned_to
        store.add(Task(id="T1", title="x", assigned_to="dev", status="in_progress"))

        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", replicas=2, backend="mock")],
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        Orchestrator(state_dir, cfg, transport)  # init triggers migration

        task = store.get("T1")
        assert task.assigned_to == "dev-1"

    def test_non_legacy_assigned_to_not_touched(self, state_dir: Path):
        """Already-instance-id assigned_to should stay as is."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev-2", status="in_progress"))

        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", replicas=2, backend="mock")],
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        Orchestrator(state_dir, cfg, transport)

        task = store.get("T1")
        assert task.assigned_to == "dev-2"

    def test_single_instance_legacy_unchanged(self, state_dir: Path):
        """With replicas=1, instance_id equals name, so assigned_to='dev'
        is already valid; migration must be a no-op."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev", status="in_progress"))

        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],  # replicas=1 default
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        Orchestrator(state_dir, cfg, transport)

        task = store.get("T1")
        assert task.assigned_to == "dev"  # unchanged
