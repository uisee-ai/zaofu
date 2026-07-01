"""End-to-end multi-instance tests (G-INST-10).

Validates the full Sprint C chain: a config with `dev replicas=3`
expands to 3 independent workers, each can be assigned tasks
independently, each has its own transport entry, WIP slot, stuck
detector, and cost bucket.
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
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport, make_transport


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
def three_dev_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", replicas=3, backend="mock"),
            RoleConfig(name="review", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


class TestReplicasExpansion:
    def test_config_expands_to_three_dev_instances(self, three_dev_config):
        instance_ids = [r.instance_id for r in three_dev_config.roles]
        assert instance_ids == ["dev-1", "dev-2", "dev-3", "review"]


class TestIndependentDispatch:
    def test_each_dev_handles_its_own_task(
        self, state_dir: Path, three_dev_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="a", assigned_to="dev-1"))
        store.add(Task(id="T2", title="b", assigned_to="dev-2"))
        store.add(Task(id="T3", title="c", assigned_to="dev-3"))

        orch = Orchestrator(state_dir, three_dev_config, transport)
        # One dispatch per run_once call (break after first)
        orch.run_once()
        orch.run_once()
        orch.run_once()

        t1 = store.get("T1")
        t2 = store.get("T2")
        t3 = store.get("T3")
        # All three should now be in_progress with distinct assignees
        statuses = [t1.status, t2.status, t3.status]
        assigned = [t1.assigned_to, t2.assigned_to, t3.assigned_to]
        assert statuses == ["in_progress"] * 3
        assert set(assigned) == {"dev-1", "dev-2", "dev-3"}


class TestIndependentStuckDetectors:
    def test_each_instance_has_own_stuck_detector(
        self, state_dir: Path, three_dev_config, transport
    ):
        orch = Orchestrator(state_dir, three_dev_config, transport)
        assert "dev-1" in orch._stuck_detectors
        assert "dev-2" in orch._stuck_detectors
        assert "dev-3" in orch._stuck_detectors
        assert "review" in orch._stuck_detectors
        # Three independent detector instances (not aliased)
        assert (
            orch._stuck_detectors["dev-1"]
            is not orch._stuck_detectors["dev-2"]
        )


class TestCostBreakdownByInstance:
    def test_cost_tracker_splits_dev_replicas(self, state_dir: Path):
        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage(role="dev", input_tokens=100, output_tokens=50,
                             instance_id="dev-1")
        tracker.record_usage(role="dev", input_tokens=200, output_tokens=75,
                             instance_id="dev-2")
        tracker.record_usage(role="dev", input_tokens=50, output_tokens=25,
                             instance_id="dev-3")

        inst = tracker.per_instance_totals()
        role = tracker.per_role_totals()

        # Instance view: 3 separate entries
        assert inst["dev-1"].input_tokens == 100
        assert inst["dev-2"].input_tokens == 200
        assert inst["dev-3"].input_tokens == 50

        # Role view: aggregated under "dev"
        assert role["dev"].input_tokens == 350
        assert role["dev"].entries == 3


class TestMakeTransportCompositeByInstance:
    def test_three_dev_replicas_each_in_composite(
        self, three_dev_config, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".zf").mkdir()
        router = make_transport(three_dev_config, dry_run=True)
        for instance_id in ("dev-1", "dev-2", "dev-3", "review"):
            # Each must be routable without raising
            router.capture_log(instance_id, lines=10)


class TestLegacyConfigUnchanged:
    def test_single_replica_legacy_config_unchanged(
        self, state_dir: Path, transport
    ):
        """A plain dev/review config (no replicas) must behave exactly as
        before Sprint C — assigned_to='dev' dispatches without migration."""
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="dev", backend="mock"),
                RoleConfig(name="review", backend="mock"),
            ],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))

        orch = Orchestrator(state_dir, cfg, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatches) == 1
        assert dispatches[0].role == "dev"
        assert store.get("T1").assigned_to == "dev"  # unchanged
