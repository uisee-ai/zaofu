"""Tests for G-INST-4: WIP + dispatch honor instance_id, not role.name.

When a role has replicas > 1, each instance must have its own WIP slot.
`_find_available_role` must iterate over instance_ids (not dedupe by
name) and `_find_role_by_name` must be replaced with an instance-aware
equivalent for the assigned_to lookup path.
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


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


@pytest.fixture
def two_dev_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", replicas=2),
            RoleConfig(name="review", replicas=1),
        ],
    )


class TestTwoDevReplicasHaveIndependentWip:
    def test_config_expands_to_two_dev_instances(self, two_dev_config):
        inst_ids = [r.instance_id for r in two_dev_config.roles]
        assert "dev-1" in inst_ids
        assert "dev-2" in inst_ids

    def test_dev_1_busy_does_not_block_dev_2(
        self, state_dir: Path, two_dev_config, transport
    ):
        """dev-1 is in_progress on T1. dev-2 should still be able to
        accept T2. With name-based WIP, both would share a single slot."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="first", status="in_progress",
                       assigned_to="dev-1"))
        store.add(Task(id="T2", title="second", assigned_to="dev-2"))

        orch = Orchestrator(state_dir, two_dev_config, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        # T2 should have been dispatched to dev-2
        assert any(d.task_id == "T2" and d.role == "dev-2" for d in dispatches)
        assert store.get("T2").status == "in_progress"

    def test_both_dev_replicas_busy_blocks_new_dispatch(
        self, state_dir: Path, two_dev_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="first", status="in_progress",
                       assigned_to="dev-1"))
        store.add(Task(id="T2", title="second", status="in_progress",
                       assigned_to="dev-2"))
        store.add(Task(id="T3", title="third", assigned_to="dev-1"))  # waits

        orch = Orchestrator(state_dir, two_dev_config, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert not any(d.task_id == "T3" for d in dispatches)
        assert store.get("T3").status == "backlog"


class TestAssignedToIsInstanceId:
    def test_assigned_to_dev_2_routes_to_dev_2(
        self, state_dir: Path, two_dev_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev-2"))

        orch = Orchestrator(state_dir, two_dev_config, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatches) == 1
        assert dispatches[0].role == "dev-2"

    def test_assigned_to_unknown_instance_rejected(
        self, state_dir: Path, two_dev_config, transport
    ):
        """assigned_to names an instance that doesn't exist → don't dispatch."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev-99"))

        orch = Orchestrator(state_dir, two_dev_config, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert dispatches == []
        assert store.get("T1").status == "backlog"


class TestLegacySingleInstanceStillWorks:
    def test_single_replica_config_unchanged(self, state_dir: Path, transport):
        """A plain config with no replicas should behave exactly as before:
        task.assigned_to = 'dev' dispatches to the single dev instance."""
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))

        orch = Orchestrator(state_dir, cfg, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatches) == 1
        assert dispatches[0].role == "dev"
