"""End-to-end tests for G-RECYCLE-9: context recycle scenarios.

Covers the full chain from high context detection to rotated session_id
to new spawn to recovery briefing injection, across Claude + Codex.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    SessionConfig,
    WorkdirConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.memory.store import MemoryStore
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.backend_session_reader import UsageReport
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.transport import TransportAdapter, AttachHandle


class _FakeTransport(TransportAdapter):
    def __init__(self):
        self.alive_flags: dict[str, bool] = {}
        self.spawn_calls: list[tuple[str, list[str]]] = []
        self.spawn_cwds: list[tuple[str, Path | None]] = []
        self.send_task_calls: list[tuple[str, str]] = []
        self.terminate_calls: list[str] = []
        self.compact_calls: list[tuple[str, str]] = []
        self.compact_result = True
        self.logs: dict[str, str] = {}

    def init(self): pass
    def is_session_running(self): return True

    def spawn(self, role, argv, *, cwd=None):
        self.spawn_calls.append((role.instance_id, argv))
        self.spawn_cwds.append((role.instance_id, cwd))
        self.alive_flags[role.instance_id] = True

    def is_alive(self, role_name):
        return self.alive_flags.get(role_name, True)

    def wait_ready(self, role_name, pattern, timeout): return True

    def send_task(self, role_name, briefing_path, prompt):
        self.send_task_calls.append((role_name, str(briefing_path)))

    def compact_context(self, role_name: str, command: str) -> bool:
        self.compact_calls.append((role_name, command))
        return self.compact_result

    def capture_log(self, role_name, lines=200): return self.logs.get(role_name, "")
    def poll_events(self): return []
    def attach_handle(self, role_name): return AttachHandle()
    def terminate(self, role_name):
        self.terminate_calls.append(role_name)
        self.alive_flags[role_name] = False
    def shutdown(self): pass


class _FakeReader:
    def __init__(self, report):
        self._report = report

    def session_path(self, *a, **kw):
        return Path("/tmp/fake")

    def read_latest_usage(self, *a, **kw):
        return self._report


def _usage(ratio: float, window: int = 200_000) -> UsageReport:
    return UsageReport(
        effective_input_tokens=int(ratio * window),
        output_tokens=100,
        model_context_window=window,
        ratio=ratio,
        timestamp="2026-04-15T10:00:00Z",
        raw={"input_tokens": int(ratio * window)},
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")


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
def claude_config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(
                name="dev",
                backend="claude-code",
                recycle_threshold=0.5,
                recycle_hard_cap=0.9,
            ),
        ],
    )


@pytest.fixture
def claude_worktree_config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(
                name="dev",
                backend="claude-code",
                role_kind="writer",
                recycle_threshold=0.5,
                recycle_hard_cap=0.9,
            ),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(
                enabled=True,
                root=".zf/workdirs",
                mode="worktree",
            ),
        ),
    )


class TestIdleRecycleFullChain:
    def test_high_ratio_idle_triggers_full_recycle(
        self, state_dir, claude_config
    ):
        transport = _FakeTransport()
        transport.spawn_calls.append(("dev", []))

        mem = MemoryStore(state_dir / "memory")
        mem.add(role="dev", mem_type="decision", content="use bcrypt")

        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.75))}
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        old_uuid = str(reg.get_or_create("dev"))
        reg.mark_spawned("dev")

        orch._check_context_thresholds()

        # The three lifecycle events must all appear
        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.context.warning" in types
        assert "worker.recycling" in types
        assert "worker.recycled" in types

        # session_id rotated
        reg2 = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        new_uuid = str(reg2.get("dev"))
        assert new_uuid != old_uuid

        # Recovery briefing written
        briefing_path = state_dir / "briefings" / "dev-recovery.md"
        assert briefing_path.exists()
        assert "bcrypt" in briefing_path.read_text()
        assert transport.send_task_calls == []

        instructions_path = state_dir / "instructions" / "dev.md"
        assert instructions_path.exists()
        assert "## Current Task" not in instructions_path.read_text()

    def test_context_recycle_preserves_configured_worktree_cwd(
        self, state_dir, claude_worktree_config
    ):
        _init_repo(state_dir.parent)
        transport = _FakeTransport()
        transport.spawn_calls.append(("dev", []))

        orch = Orchestrator(state_dir, claude_worktree_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.75))}
        reg = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        reg.get_or_create("dev")
        reg.mark_spawned("dev")

        orch._check_context_thresholds()

        expected = state_dir / "workdirs" / "dev" / "project"
        assert ("dev", expected) in transport.spawn_cwds
        assert expected.exists()
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(
            e.type == "workdir.prepared"
            and e.payload.get("instance_id") == "dev"
            and e.payload.get("source") == "context_recycle"
            for e in events
        )


class TestBusyRecycleWaitsForIdle:
    def test_busy_worker_uses_compact_before_recycle(
        self,
        state_dir,
        claude_config,
    ):
        transport = _FakeTransport()
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
        ))
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert transport.compact_calls == [("dev", "/compact")]
        assert "worker.context.compact.requested" in types
        assert "worker.context.compacted" in types
        assert "worker.recycling" not in types
        assert orch._instance_state["dev"] == "healthy"
        assert transport.send_task_calls

    def test_codex_busy_worker_defers_compact_until_idle(
        self,
        state_dir,
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="arch",
                    backend="codex",
                    recycle_threshold=0.5,
                    recycle_hard_cap=0.9,
                ),
            ],
        )
        transport = _FakeTransport()
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="arch",
            active_dispatch_id="disp-1",
        ))
        orch = Orchestrator(state_dir, cfg, transport)
        orch._session_readers = {"codex": _FakeReader(_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("arch")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert transport.compact_calls == []
        assert "worker.context.compact.requested" not in types
        assert "worker.context.compacted" not in types
        failed = [event for event in events if event.type == "worker.context.compact.failed"]
        assert failed
        assert failed[-1].payload["error"] == "backend compact requires idle session"
        assert orch._instance_state["arch"] == "pending_recycle"

    def test_transport_compact_denial_does_not_emit_compacted(
        self,
        state_dir,
        claude_config,
    ):
        transport = _FakeTransport()
        transport.logs["dev"] = "`/compact` is disabled while a task is in progress"
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [event.type for event in events]
        assert transport.compact_calls == [("dev", "/compact")]
        assert "worker.context.compact.requested" in types
        assert "worker.context.compacted" not in types
        assert "worker.context.compact.failed" in types
        assert orch._instance_state["dev"] == "pending_recycle"

    def test_recycle_deferred_until_task_done(self, state_dir, claude_config):
        transport = _FakeTransport()
        transport.spawn_calls.append(("dev", []))

        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))

        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")
        orch._context_compact_attempted = {"dev"}

        orch._check_context_thresholds()
        assert orch._instance_state["dev"] == "pending_recycle"
        types_before = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
        assert "worker.recycling" not in types_before

        # Task completes, making dev idle
        store.update("T1", status="done")
        orch._check_pending_recycles()

        types_after = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
        assert "worker.recycling" in types_after
        assert "worker.recycled" in types_after
        assert orch._instance_state["dev"] == "healthy"

    def test_role_name_assigned_task_uses_dispatched_instance_for_busy_check(
        self,
        state_dir,
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="dev",
                    instance_id="dev-1",
                    backend="claude-code",
                    recycle_threshold=0.5,
                    recycle_hard_cap=0.9,
                ),
            ],
        )
        transport = _FakeTransport()
        transport.spawn_calls.append(("dev-1", []))
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T1",
            payload={"role": "dev-1", "dispatch_id": "disp-1"},
        ))

        orch = Orchestrator(state_dir, cfg, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.75))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev-1")
        orch._context_compact_attempted = {"dev-1"}

        orch._check_context_thresholds()

        assert orch._instance_state["dev-1"] == "pending_recycle"
        events = EventLog(state_dir / "events.jsonl").read_all()
        warnings = [e for e in events if e.type == "worker.context.warning"]
        assert warnings[-1].actor == "dev-1"
        assert warnings[-1].payload["idle"] is False
        assert not any(e.type == "worker.recycling" for e in events)

    def test_forced_busy_recycle_returns_worker_to_busy_state(
        self,
        state_dir,
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="dev",
                    instance_id="dev-1",
                    backend="claude-code",
                    recycle_threshold=0.5,
                    recycle_hard_cap=0.9,
                    drain_hold_seconds=0.0,
                ),
            ],
        )
        transport = _FakeTransport()
        transport.spawn_calls.append(("dev-1", []))
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T1",
            payload={"role": "dev-1", "dispatch_id": "disp-1"},
        ))

        orch = Orchestrator(state_dir, cfg, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.95))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev-1")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        state_events = [
            e for e in events
            if e.type == "worker.state.changed" and e.actor == "dev-1"
        ]
        assert transport.send_task_calls
        assert any(e.type == "worker.recycled" for e in events)
        assert state_events[-1].payload["to"] == "busy"
        assert state_events[-1].payload["reason"] == (
            "recycle complete; resumed task T1"
        )

    def test_forced_busy_recycle_blocks_when_recovery_contract_insufficient(
        self,
        state_dir,
    ):
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="dev",
                    instance_id="dev-1",
                    backend="claude-code",
                    role_kind="writer",
                    recycle_threshold=0.5,
                    recycle_hard_cap=0.9,
                    drain_hold_seconds=0.0,
                ),
            ],
            workflow=WorkflowConfig(
                dag=WorkflowDagConfig(
                    dev_requires_orchestrator_backlog=True,
                    required_backlog_refs=["plan_ref"],
                ),
            ),
        )
        transport = _FakeTransport()
        transport.spawn_calls.append(("dev-1", []))
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1",
            title="x",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-1",
            contract=Task().contract,
        ))
        task = store.get("T1")
        assert task is not None
        task.contract.owner_role = "dev"
        store.update("T1", contract=task.contract)
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="T1",
            payload={"role": "dev-1", "dispatch_id": "disp-1"},
        ))

        orch = Orchestrator(state_dir, cfg, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.95))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev-1")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.recovery.insufficient" in types
        assert "recovery.contract.rehydrate.requested" in types
        assert "recovery.contract.rehydrated" in types
        assert "worker.recovery.blocked" in types
        assert "worker.recycle.failed" in types
        assert transport.send_task_calls == []


class TestHardCapBusyStillNoInterrupt:
    def test_95_percent_while_busy_emits_critical_but_no_interrupt(
        self, state_dir, claude_config
    ):
        transport = _FakeTransport()
        transport.spawn_calls.append(("dev", []))

        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))

        orch = Orchestrator(state_dir, claude_config, transport)
        orch._session_readers = {"claude-code": _FakeReader(_usage(0.95))}
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        ).get_or_create("dev")

        orch._check_context_thresholds()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.context.warning" in types
        assert "worker.context.critical" in types
        # Still no recycling yet (busy)
        assert "worker.recycling" not in types
        # Task still in_progress — not interrupted
        assert store.get("T1").status == "in_progress"
