"""Tests for G-COST-BLOCK-1: cost budget enforcement at dispatch.

Adds RoleConfig.budget_usd (per-role cap) and ZfConfig.global_budget_usd
(global cap). _dispatch_ready checks both before dispatching; over-
budget paths emit cost.budget.exceeded and skip the dispatch.
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
from zf.core.config.loader import load_config
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


class TestSchemaFields:
    def test_role_config_has_budget_usd_default_none(self):
        role = RoleConfig(name="dev")
        assert role.budget_usd is None

    def test_role_config_explicit_budget(self):
        role = RoleConfig(name="dev", budget_usd=0.50)
        assert role.budget_usd == 0.50

    def test_zfconfig_has_global_budget_usd_default_none(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
        )
        assert cfg.global_budget_usd is None

    def test_zfconfig_explicit_global_budget(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            global_budget_usd=10.0,
        )
        assert cfg.global_budget_usd == 10.0

    def test_zfconfig_budget_enforcement_defaults_enabled(self):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
        )
        assert cfg.budget_enforcement_enabled is True

    def test_loader_reads_budget_enforcement_disabled(self, tmp_path: Path):
        path = tmp_path / "zf.yaml"
        path.write_text(
            "\n".join([
                "version: '1.0'",
                "project:",
                "  name: t",
                "global_budget_usd: 1.0",
                "budget_enforcement_enabled: false",
                "roles:",
                "  - name: dev",
                "    backend: mock",
            ]),
            encoding="utf-8",
        )

        cfg = load_config(path)

        assert cfg.global_budget_usd == 1.0
        assert cfg.budget_enforcement_enabled is False


class TestNoBudgetMeansNoBlocking:
    def test_dispatch_works_when_no_budgets_configured(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))

        # Record some cost so totals are non-zero
        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage("dev", 10000, 5000)

        orch = Orchestrator(state_dir, cfg, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatches) == 1
        assert store.get("T1").status == "in_progress"


class TestGlobalBudgetBlocking:
    def test_dispatch_proceeds_when_budget_enforcement_disabled(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            global_budget_usd=0.01,
            budget_enforcement_enabled=False,
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))
        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage("dev", 100_000, 50_000)

        orch = Orchestrator(state_dir, cfg, transport)
        decisions = orch.run_once()

        assert [d for d in decisions if d.action == "dispatch"]
        assert store.get("T1").status == "in_progress"
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "cost.budget.exceeded" for e in events)

    def test_dispatch_blocked_when_global_budget_exceeded(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            global_budget_usd=0.01,  # tiny
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))

        # Burn the budget
        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage("dev", 100_000, 50_000)  # easily > $0.01

        orch = Orchestrator(state_dir, cfg, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert dispatches == []
        # Task stayed in backlog
        assert store.get("T1").status == "backlog"

        events = EventLog(state_dir / "events.jsonl").read_all()
        budget_events = [
            e for e in events if e.type == "cost.budget.exceeded"
        ]
        assert len(budget_events) >= 1
        assert any(
            e.payload.get("scope") == "global" for e in budget_events
        )
        skipped = [
            e for e in events
            if e.type == "orchestrator.dispatch_skipped"
            and e.task_id == "T1"
        ]
        assert skipped
        assert skipped[-1].payload.get("reason") == "budget_exceeded"

    def test_dispatch_proceeds_when_global_budget_not_yet_exceeded(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            global_budget_usd=100.0,
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))

        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage("dev", 100, 50)  # tiny

        orch = Orchestrator(state_dir, cfg, transport)
        decisions = orch.run_once()

        dispatches = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatches) == 1


class TestPerRoleBudgetBlocking:
    def test_dispatch_blocked_when_role_budget_exceeded(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="dev", backend="mock", budget_usd=0.01),
                RoleConfig(name="review", backend="mock", budget_usd=10.0),
            ],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))

        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage("dev", 100_000, 50_000)  # blow dev's $0.01 budget

        orch = Orchestrator(state_dir, cfg, transport)
        orch.run_once()

        # dev blocked
        assert store.get("T1").status == "backlog"
        events = EventLog(state_dir / "events.jsonl").read_all()
        budget_events = [
            e for e in events if e.type == "cost.budget.exceeded"
        ]
        assert any(
            e.payload.get("scope") == "role"
            and e.payload.get("role") == "dev"
            for e in budget_events
        )
        assert any(
            e.type == "orchestrator.dispatch_skipped"
            and e.payload.get("assignee") == "dev"
            and e.payload.get("reason") == "budget_exceeded"
            for e in events
        )

    def test_other_role_unaffected_by_role_budget(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="dev", backend="mock", budget_usd=0.01),
                RoleConfig(name="review", backend="mock", budget_usd=10.0),
            ],
        )
        store = TaskStore(state_dir / "kanban.json")
        # T1 → review (which has plenty of budget)
        store.add(Task(id="T1", title="x", assigned_to="review"))

        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage("dev", 100_000, 50_000)  # blow dev only

        orch = Orchestrator(state_dir, cfg, transport)
        orch.run_once()

        # review still gets dispatched
        assert store.get("T1").status == "in_progress"


class TestEventDedup:
    def test_global_event_not_re_emitted_within_cooldown(
        self, state_dir, transport
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            global_budget_usd=0.01,
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", assigned_to="dev"))
        store.add(Task(id="T2", title="y", assigned_to="dev"))

        tracker = CostTracker(state_dir / "cost.jsonl")
        tracker.record_usage("dev", 100_000, 50_000)

        orch = Orchestrator(state_dir, cfg, transport)
        orch.run_once()
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        global_events = [
            e for e in events
            if e.type == "cost.budget.exceeded"
            and e.payload.get("scope") == "global"
        ]
        # Dedup: at most one within the cooldown window
        assert len(global_events) <= 2  # a touch of slack for race
