"""Tests for G-LIFE-1: new orchestrator handlers for judge/dev.blocked/gate.failed.

Legacy mode (no orchestrator role in config) — these tests verify that
the Python kernel's `_event_handlers()` dict routes the four missing
events to their new `_on_*` methods and the task state machine advances
correctly. Layer 2 mode is covered separately by tests around
`_notify_orchestrator_agent`.
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
from zf.core.task.schema import Task, TaskContract
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
def legacy_config() -> ZfConfig:
    """No orchestrator role → Python kernel handles events directly."""
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
            RoleConfig(name="test", backend="mock"),
            RoleConfig(name="judge", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _emit(state_dir: Path, event: ZfEvent) -> None:
    EventLog(state_dir / "events.jsonl").append(event)


class TestJudgePassed:
    def test_judge_passed_moves_testing_to_done(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="test"))

        _emit(state_dir, ZfEvent(type="judge.passed", actor="judge", task_id="T1"))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task is not None, "task must exist"
        assert task.status == "done"

    def test_candidate_level_judge_passed_moves_feature_tasks_to_done(
        self, state_dir: Path, legacy_config, transport
    ):
        # PRD/issue/refactor fanout emit a candidate/PDD-level judge.passed with
        # NO task_id (ledger PRD e2e 2026-06-20): cards stayed in_progress
        # forever. The candidate's canonical tasks (matched by feature_id) must
        # move to done; sibling tasks of a different feature must not.
        from zf.core.task.schema import TaskContract

        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="PDD-A-001", title="impl", status="in_progress",
            assigned_to="dev-api", contract=TaskContract(feature_id="feat-x"),
        ))
        store.add(Task(
            id="PDD-A-002", title="web", status="in_progress",
            assigned_to="dev-web", contract=TaskContract(feature_id="feat-x"),
        ))
        store.add(Task(
            id="PDD-A-003", title="verified but not projected", status="review",
            assigned_to="verify-web", contract=TaskContract(feature_id="feat-x"),
        ))
        store.add(Task(
            id="PDD-B-001", title="other", status="in_progress",
            assigned_to="dev-api", contract=TaskContract(feature_id="feat-y"),
        ))

        # candidate/PDD-level shape: kernel-emitted aggregate, no task_id, has
        # fanout_id (passes the worker-lifecycle task-binding validator via the
        # zf-cli actor branch) + pdd_id + feature_id.
        _emit(state_dir, ZfEvent(
            type="judge.passed", actor="zf-cli",
            payload={
                "fanout_id": "fanout-prd-judge-1",
                "stage_id": "prd-judge",
                "pdd_id": "PDD-A",
                "feature_id": "feat-x",
            },
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        assert store.get("PDD-A-001").status == "done"
        assert store.get("PDD-A-002").status == "done"
        assert store.get("PDD-A-003").status == "done"
        # different feature_id untouched
        assert store.get("PDD-B-001").status == "in_progress"

    def test_candidate_level_judge_passed_closes_container_task_by_id(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="TASK-D27C0B",
            title="candidate container",
            status="in_progress",
            assigned_to="zf-cli",
            contract=TaskContract(feature_id="F-9a50a629"),
        ))
        store.add(Task(
            id="PDD-TODO-001",
            title="impl",
            status="in_progress",
            assigned_to="dev-api",
            contract=TaskContract(feature_id="TASK-D27C0B"),
        ))

        _emit(state_dir, ZfEvent(
            type="judge.passed", actor="zf-cli",
            payload={
                "fanout_id": "fanout-prd-judge-1",
                "stage_id": "prd-judge",
                "pdd_id": "PDD-TODO-001",
                "feature_id": "TASK-D27C0B",
            },
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        assert store.get("TASK-D27C0B").status == "done"
        assert store.get("PDD-TODO-001").status == "done"

    def test_candidate_level_judge_passed_closes_workflow_bootstrap_root_card(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="ISSUE-WF-1",
            title="workflow root",
            status="backlog",
            contract=TaskContract(
                feature_id="issue-regression",
                evidence_contract={
                    "source": "workflow_invoke_bootstrap",
                    "workflow_fanout_anchor": True,
                },
            ),
        ))
        store.add(Task(
            id="TASK-CHILD-1",
            title="child",
            status="testing",
            assigned_to="verify-lane-0",
            contract=TaskContract(feature_id="issue-regression"),
        ))
        store.add(Task(
            id="TASK-OTHER",
            title="other backlog",
            status="backlog",
            contract=TaskContract(feature_id="issue-regression"),
        ))

        _emit(state_dir, ZfEvent(
            type="judge.passed",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-issue-final-1",
                "stage_id": "issue-final",
                "pdd_id": "ISSUE-WF-1",
                "feature_id": "issue-regression",
            },
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        assert store.get("ISSUE-WF-1").status == "done"
        assert store.get("TASK-CHILD-1").status == "done"
        assert store.get("TASK-OTHER").status == "backlog"


class TestJudgeFailed:
    def test_judge_failed_returns_task_to_in_progress(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="test"))

        _emit(state_dir, ZfEvent(
            type="judge.failed", actor="judge", task_id="T1",
            payload={"reason": "rubric item 3 missing"},
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task is not None
        assert task.status == "in_progress"

    def test_judge_failed_no_op_if_task_not_in_testing(
        self, state_dir: Path, legacy_config, transport
    ):
        """Defensive: only react if task is actually in testing state.
        Use review status (not backlog) so _dispatch_ready doesn't
        auto-pick-up the task in legacy mode and skew the assertion."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="review", assigned_to="review"))

        _emit(state_dir, ZfEvent(type="judge.failed", actor="judge", task_id="T1"))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task.status == "review"
        assert task.assigned_to == "review"
        assert task.retry_count == 0


class TestDevBlocked:
    def test_dev_blocked_marks_task_blocked(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))

        _emit(state_dir, ZfEvent(
            type="dev.blocked", actor="dev", task_id="T1",
            payload={"reason": "missing API key for OAuth provider"},
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task is not None
        assert task.status == "blocked"

    def test_dev_blocked_no_op_on_unknown_task(
        self, state_dir: Path, legacy_config, transport
    ):
        _emit(state_dir, ZfEvent(
            type="dev.blocked", actor="dev", task_id="T-ghost",
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        # Must not crash
        orch.run_once()


class TestGateFailed:
    def test_gate_failed_returns_task_to_in_progress_from_review(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="review", assigned_to="review"))

        _emit(state_dir, ZfEvent(
            type="gate.failed", actor="zf-cli", task_id="T1",
            payload={"gate": "pytest", "reason": "3 tests failing"},
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task is not None
        assert task.status == "in_progress"

    def test_gate_failed_returns_task_to_in_progress_from_testing(
        self, state_dir: Path, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="test"))

        _emit(state_dir, ZfEvent(
            type="gate.failed", actor="zf-cli", task_id="T1",
            payload={"gate": "mypy", "reason": "type error in auth.py"},
        ))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        assert store.get("T1").status == "in_progress"

    def test_gate_failed_noop_on_terminal_task(
        self, state_dir: Path, legacy_config, transport
    ):
        """A gate.failed event against an already-done task must not
        un-done it (done tasks live in archive, plus semantics are wrong)."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing"))
        store.update("T1", status="done")  # → archived

        _emit(state_dir, ZfEvent(type="gate.failed", actor="zf-cli", task_id="T1"))

        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task.status == "done"  # unchanged


class TestHandlersRegistered:
    def test_event_handlers_dict_includes_new_entries(
        self, state_dir: Path, legacy_config, transport
    ):
        """All 4 new event types must be routed."""
        orch = Orchestrator(state_dir, legacy_config, transport)
        handlers = orch._event_handlers()
        assert "judge.passed" in handlers
        assert "judge.failed" in handlers
        assert "dev.blocked" in handlers
        assert "gate.failed" in handlers
