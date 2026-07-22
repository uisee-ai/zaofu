"""Tests for G-WIRE-1: ScopeRatchet wired into dispatch / completion.

Snapshot the workspace on dispatch, diff + check on *.done events
against task.contract.scope/exclusions, emit scope.violation per
out-of-scope file.
"""

from __future__ import annotations

import inspect
import os
import time
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ScopeVerificationConfig,
    VerificationConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.verification.scope_ratchet import ScopeRatchet, ScopeSnapshot
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A fake workspace with a couple of seeded files."""
    ws = tmp_path
    (ws / "src").mkdir()
    (ws / "src" / "auth.py").write_text("# auth module\n")
    (ws / "src" / "other.py").write_text("# other module\n")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_auth.py").write_text("# auth test\n")
    return ws


@pytest.fixture
def state_dir(workspace: Path) -> Path:
    sd = workspace / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(workspace))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


class TestImportProof:
    def test_scope_ratchet_imported_by_orchestrator(self):
        """Wire-up discipline: ScopeRatchet must be referenced in the
        orchestrator runtime module."""
        from zf.runtime import orchestrator
        src = inspect.getsource(orchestrator)
        assert "ScopeRatchet" in src, (
            "ScopeRatchet must be imported and used by orchestrator.py "
            "(library-without-callers anti-pattern check)"
        )


class TestSnapshotOnDispatch:
    def test_dispatch_takes_workspace_snapshot(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth work", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()  # dispatches T1

        # Snapshot must have been taken and stashed by task id
        assert "T1" in orch._scope_snapshots
        snap = orch._scope_snapshots["T1"]
        assert isinstance(snap, ScopeSnapshot)
        # Should include the seeded file
        assert any("src/auth.py" in p for p in snap.files)


class TestDoneEventTriggersCheck:
    def test_custom_state_dir_activity_is_not_worker_scope(
        self, state_dir, workspace, config, transport
    ):
        custom_state_dir = workspace / ".zf-custom"
        state_dir.rename(custom_state_dir)
        store = TaskStore(custom_state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))

        orch = Orchestrator(custom_state_dir, config, transport)
        orch.run_once()
        runtime_projection = custom_state_dir / "projections" / "health.json"
        runtime_projection.parent.mkdir(parents=True, exist_ok=True)
        runtime_projection.write_text("{}\n")

        log = EventLog(custom_state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        violations = [
            event for event in log.read_all()
            if event.type == "scope.violation"
        ]
        assert violations == []
        assert ".zf-custom" in orch._scope_ratchet.ignore_prefixes

    def test_in_scope_change_emits_no_violation(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()  # dispatch + snapshot

        # Modify an in-scope file
        time.sleep(0.01)
        (workspace / "src" / "auth.py").write_text("# auth module v2\n")
        os.utime(workspace / "src" / "auth.py", None)

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="dev.build.done", actor="dev", task_id="T1",
        ))
        orch.run_once()

        events = log.read_all()
        violations = [e for e in events if e.type == "scope.violation"]
        assert violations == []

    def test_out_of_scope_change_emits_violation(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()  # dispatch + snapshot

        # Modify an out-of-scope file
        time.sleep(0.01)
        (workspace / "src" / "other.py").write_text("# OOPS modified\n")
        os.utime(workspace / "src" / "other.py", None)

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        events = log.read_all()
        violations = [e for e in events if e.type == "scope.violation"]
        assert len(violations) >= 1
        v_paths = []
        for v in violations:
            paths = v.payload.get("paths") or [v.payload.get("path", "")]
            v_paths.extend(paths)
        assert any("src/other.py" in p for p in v_paths)

    def test_out_of_scope_change_fail_closed_blocks_review_handoff(
        self, state_dir, workspace, transport
    ):
        strict_config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            verification=VerificationConfig(
                scope=ScopeVerificationConfig(fail_closed=True),
            ),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))

        orch = Orchestrator(state_dir, strict_config, transport)
        orch.run_once()
        time.sleep(0.01)
        (workspace / "src" / "other.py").write_text("# OOPS modified\n")
        os.utime(workspace / "src" / "other.py", None)

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        assert store.get("T1").status == "in_progress"
        events = log.read_all()
        assert any(e.type == "scope.violation" for e in events)
        assert any(
            e.type == "gate.failed"
            and e.payload.get("gate") == "scope"
            for e in events
        )

    def test_blocked_path_emits_violation(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(
                scope=["src"],
                exclusions=["src/other.py"],
            ),
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        time.sleep(0.01)
        (workspace / "src" / "other.py").write_text("# blocked!\n")
        os.utime(workspace / "src" / "other.py", None)

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        events = log.read_all()
        violations = [e for e in events if e.type == "scope.violation"]
        assert len(violations) >= 1


class TestNoContractScope:
    def test_no_scope_means_no_check(
        self, state_dir, workspace, config, transport
    ):
        """Tasks without a contract.scope are unconstrained — modifying
        any file must not produce violations."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="freeform", assigned_to="dev"))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        time.sleep(0.01)
        (workspace / "src" / "other.py").write_text("# anything goes\n")
        os.utime(workspace / "src" / "other.py", None)

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        events = log.read_all()
        violations = [e for e in events if e.type == "scope.violation"]
        assert violations == []


class TestSnapshotCleanup:
    def test_snapshot_removed_after_check(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", assigned_to="dev",
            contract=TaskContract(scope=["src"]),
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()
        assert "T1" in orch._scope_snapshots

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        # Snapshot consumed
        assert "T1" not in orch._scope_snapshots


class TestScopeCheckErrorFailClosed:
    """2026-06-10 review (I4): an errored scope check returned [] — under
    fail_closed config "could not verify" must be treated as a violation,
    not silently waved through to review."""

    def _strict_config(self):
        return ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            verification=VerificationConfig(
                scope=ScopeVerificationConfig(fail_closed=True),
            ),
            roles=[RoleConfig(name="dev", backend="mock")],
        )

    def test_scope_check_error_fails_closed(
        self, state_dir, workspace, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))
        orch = Orchestrator(state_dir, self._strict_config(), transport)
        orch.run_once()  # dispatch + snapshot

        def _boom():
            raise OSError("permission denied during rglob")

        orch._scope_ratchet.snapshot = _boom

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        events = log.read_all()
        gate_failed = [
            e for e in events
            if e.type == "gate.failed" and e.payload.get("gate") == "scope"
        ]
        assert gate_failed, "errored scope check must fail closed"
        reasons = [
            v.get("reason")
            for e in gate_failed
            for v in e.payload.get("violations", [])
        ]
        assert any("scope_check_errored" in str(r) for r in reasons)
        assert store.get("T1").status != "review"

    def test_scope_check_error_stays_observational_by_default(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))
        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        def _boom():
            raise OSError("permission denied during rglob")

        orch._scope_ratchet.snapshot = _boom

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()

        events = log.read_all()
        assert not [
            e for e in events
            if e.type == "gate.failed" and e.payload.get("gate") == "scope"
        ]
        assert store.get("T1").status == "review"

    def test_snapshot_failure_at_dispatch_is_observable(
        self, state_dir, workspace, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="auth", assigned_to="dev",
            contract=TaskContract(scope=["src/auth.py"]),
        ))
        orch = Orchestrator(state_dir, config, transport)

        def _boom():
            raise OSError("disk error at dispatch")

        orch._scope_ratchet.snapshot = _boom
        orch.run_once()  # dispatch attempts snapshot

        events = EventLog(state_dir / "events.jsonl").read_all()
        failed = [e for e in events if e.type == "scope.snapshot.failed"]
        assert len(failed) == 1
        assert failed[0].task_id == "T1"
        assert "disk error" in failed[0].payload["reason"]
