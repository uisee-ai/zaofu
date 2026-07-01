"""End-to-end tests for G-RESUME-6: pane crash recovery invariant X.

Covers the full chain from "pane dies" → watchdog detects → respawn
with correct --resume / exec resume semantics → recovery briefing
injected.
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
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.memory.store import MemoryStore
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.transport import TransportAdapter, AttachHandle


class _FakeTransport(TransportAdapter):
    """Full fake transport that records every call and exposes hooks
    to simulate pane death."""

    def __init__(self):
        self.alive_flags: dict[str, bool] = {}
        self.spawn_calls: list[tuple[str, list[str]]] = []
        self.spawn_cwds: list[tuple[str, Path | None]] = []
        self.send_task_calls: list[tuple[str, str]] = []
        self.terminate_calls: list[str] = []

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

    def capture_log(self, role_name, lines=200):
        return "some output"

    def poll_events(self): return []
    def attach_handle(self, role_name): return AttachHandle()

    def terminate(self, role_name):
        self.terminate_calls.append(role_name)
        self.alive_flags[role_name] = False

    def shutdown(self): pass


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


class _FailingRecoveryTransport(_FakeTransport):
    def send_task(self, role_name, briefing_path, prompt):
        raise RuntimeError("pane is at shell")


class _ContextOverflowTransport(_FakeTransport):
    def capture_log(self, role_name, lines=200):
        return (
            "■ Codex ran out of room in the model's context window. "
            "Start a new thread or clear earlier history before retrying."
        )


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
    # 2026-06-11-0325 (I41 gate): the dead-pane watchdog only recovers
    # workers with pending obligations; an idle dead pane is evidence-only
    # (the R18 completed-worker falsepos fix). These tests exercise the
    # recovery MACHINERY, so seed an active task per worker.
    store = TaskStore(sd / "kanban.json")
    store.add(Task(
        id="TASK-LIVENESS-DEV", title="busy dev",
        status="in_progress", assigned_to="dev",
        active_dispatch_id="disp-dev",
    ))
    store.add(Task(
        id="TASK-LIVENESS-REVIEW", title="busy review",
        status="in_progress", assigned_to="review",
        active_dispatch_id="disp-review",
    ))
    return sd


@pytest.fixture
def claude_dev_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="claude-code", permission_mode="bypass"),
        ],
    )


@pytest.fixture
def worktree_dev_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock", role_kind="writer"),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(
                enabled=True,
                root=".zf/workdirs",
                mode="worktree",
            ),
        ),
    )


@pytest.fixture
def codex_review_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="review", backend="codex"),
        ],
    )


class TestClaudeCrashRecoveryInvariantX:
    def test_crash_respawns_with_same_session_id_via_resume(
        self, state_dir: Path, claude_dev_config, tmp_path, monkeypatch,
    ):
        """B-W5-01 (2026-04-20): respawn must use --resume, but only if
        the claude session JSONL file actually exists. Simulate a real
        crash by creating the file under a fake HOME.
        """
        # Redirect Path.home() so claude_session_path resolves to
        # tmp_path/.claude/... and we can materialize a fake session file.
        monkeypatch.setenv("HOME", str(tmp_path))

        transport = _FakeTransport()
        transport.alive_flags["dev"] = True
        transport.spawn_calls.append(("dev", []))  # seed initial spawn

        orch = Orchestrator(state_dir, claude_dev_config, transport)

        # Prime session id in registry (simulating first spawn happened)
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        expected_uuid = str(registry.get_or_create("dev"))
        registry.mark_spawned("dev")

        # Create the fake claude session file (pane crashed but file
        # survives — the normal crash-recovery scenario).
        from zf.runtime.session_tailer import claude_session_path
        sess_path = claude_session_path(str(state_dir.parent), expected_uuid)
        sess_path.parent.mkdir(parents=True, exist_ok=True)
        sess_path.write_text('{"type":"system","subtype":"init"}\n')

        # Simulate pane death
        transport.alive_flags["dev"] = False
        for _ in range(3):
            orch.run_once()

        # Verify respawn happened
        respawn_calls = [c for c in transport.spawn_calls[1:] if c[0] == "dev"]
        assert len(respawn_calls) >= 1, (
            f"expected at least one respawn, got {transport.spawn_calls}"
        )
        respawn_argv = respawn_calls[0][1]
        # Must use --resume with the original session_id
        assert "--resume" in respawn_argv
        assert expected_uuid in respawn_argv
        # Must NOT use --session-id (that would create a new session)
        assert "--session-id" not in respawn_argv

    def test_crash_emits_worker_respawned_event(
        self, state_dir: Path, claude_dev_config
    ):
        transport = _FakeTransport()
        transport.alive_flags["dev"] = True
        transport.spawn_calls.append(("dev", []))

        orch = Orchestrator(state_dir, claude_dev_config, transport)
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        registry.get_or_create("dev")
        registry.mark_spawned("dev")

        transport.alive_flags["dev"] = False
        for _ in range(3):
            orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.respawned" in types

    def test_crash_injects_recovery_briefing(
        self, state_dir: Path, claude_dev_config
    ):
        transport = _FakeTransport()
        transport.alive_flags["dev"] = True
        transport.spawn_calls.append(("dev", []))

        # Give the recovery briefing something to pick up
        mem = MemoryStore(state_dir / "memory")
        mem.add(role="dev", mem_type="decision", content="use bcrypt for passwords")

        orch = Orchestrator(state_dir, claude_dev_config, transport)
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        registry.get_or_create("dev")
        registry.mark_spawned("dev")

        transport.alive_flags["dev"] = False
        for _ in range(3):
            orch.run_once()

        # Recovery briefing file must be created
        briefing_path = state_dir / "briefings" / "dev-recovery.md"
        assert briefing_path.exists(), "recovery briefing was not written"
        body = briefing_path.read_text()
        assert "bcrypt" in body  # the memory content propagated

        # send_task must be called with the recovery briefing
        send_paths = [c[1] for c in transport.send_task_calls]
        assert any("dev-recovery.md" in p for p in send_paths)

    def test_respawn_failed_when_recovery_briefing_cannot_be_injected(
        self, state_dir: Path, claude_dev_config
    ):
        transport = _FailingRecoveryTransport()
        transport.alive_flags["dev"] = True
        transport.spawn_calls.append(("dev", []))

        orch = Orchestrator(state_dir, claude_dev_config, transport)
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        registry.get_or_create("dev")
        registry.mark_spawned("dev")

        transport.alive_flags["dev"] = False
        for _ in range(3):
            orch.run_once()

        types = [e.type for e in EventLog(state_dir / "events.jsonl").read_all()]
        assert "worker.respawn.failed" in types
        assert "worker.respawned" not in types

    def test_respawn_preserves_configured_worktree_cwd(
        self, state_dir: Path, worktree_dev_config
    ):
        _init_repo(state_dir.parent)
        transport = _FakeTransport()
        transport.alive_flags["dev"] = True
        transport.spawn_calls.append(("dev", []))

        orch = Orchestrator(state_dir, worktree_dev_config, transport)

        transport.alive_flags["dev"] = False
        for _ in range(3):
            orch.run_once()

        expected = state_dir / "workdirs" / "dev" / "project"
        assert ("dev", expected) in transport.spawn_cwds
        assert expected.exists()
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(
            e.type == "workdir.prepared"
            and e.payload.get("instance_id") == "dev"
            and e.payload.get("source") == "watchdog_respawn"
            for e in events
        )

    def test_respawn_recovery_briefing_uses_role_worktree_git_context(
        self, state_dir: Path, worktree_dev_config
    ):
        _init_repo(state_dir.parent)
        from zf.runtime.workdirs import WorkdirManager

        role = worktree_dev_config.roles[0]
        plan = WorkdirManager(
            state_dir=state_dir,
            project_root=state_dir.parent,
            config=worktree_dev_config,
        ).prepare(role)
        project_path = Path(plan.project_path)
        artifact = project_path / "docs" / "plan.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("draft\n", encoding="utf-8")
        TaskStore(state_dir / "kanban.json").add(Task(
            id="T1",
            title="write plan",
            status="in_progress",
            assigned_to="dev",
        ))
        transport = _FakeTransport()
        transport.alive_flags["dev"] = True
        transport.spawn_calls.append(("dev", []))

        orch = Orchestrator(state_dir, worktree_dev_config, transport)

        transport.alive_flags["dev"] = False
        for _ in range(3):
            orch.run_once()

        briefing_path = state_dir / "briefings" / "dev-recovery.md"
        body = briefing_path.read_text(encoding="utf-8")
        assert "docs/plan.md" in body
        assert "Working tree**: clean" not in body


class TestOrchestratorRestartPreservesSessionId:
    def test_session_id_survives_orchestrator_restart(
        self, state_dir: Path, claude_dev_config
    ):
        transport1 = _FakeTransport()
        transport1.spawn_calls.append(("dev", []))
        orch1 = Orchestrator(state_dir, claude_dev_config, transport1)

        registry1 = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        uuid_before = str(registry1.get_or_create("dev"))

        # Simulate orchestrator shutdown — drop the object
        del orch1
        del transport1
        del registry1

        # New orchestrator from same state_dir
        transport2 = _FakeTransport()
        orch2 = Orchestrator(state_dir, claude_dev_config, transport2)
        registry2 = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        uuid_after = str(registry2.get_or_create("dev"))
        assert uuid_before == uuid_after


class TestCodexRespawnUsesCachedUuid:
    def test_codex_respawn_uses_cached_session_id(
        self, state_dir: Path, codex_review_config,
        tmp_path, monkeypatch,
    ):
        # Prime a fake codex session directory
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        observed_uuid = "77777777-7777-7777-7777-777777777777"
        folder = fake_codex / "sessions" / "2026" / "04" / "15"
        folder.mkdir(parents=True)
        (folder / f"rollout-2026-04-15T00-00-00-{observed_uuid}.jsonl").write_text("{}")

        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        registry.observe_codex_session("review", since_ts=0)
        registry.mark_spawned("review")  # simulate prior spawn

        # Codex is now a persistent TUI; respawn calls transport.spawn
        # with `codex --dangerously-... resume <observed_uuid>`.
        from zf.runtime.spawn_coordinator import SpawnCoordinator

        transport = _FakeTransport()
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root=str(state_dir.parent),
        )
        role = codex_review_config.roles[0]
        coordinator.spawn(role)  # respawn

        assert len(transport.spawn_calls) == 1
        _, argv = transport.spawn_calls[0]
        assert argv[0] == "env"
        assert f"ZF_PROJECT_ROOT={state_dir.parent.resolve()}" in argv
        assert f"ZF_STATE_DIR={state_dir.resolve()}" in argv
        assert any(arg.startswith("CODEX_HOME=") for arg in argv)
        assert "codex" in argv
        assert "resume" in argv
        resume_idx = argv.index("resume")
        assert argv[resume_idx + 1] == observed_uuid

    def test_codex_context_overflow_respawns_without_resume(
        self, state_dir: Path, codex_review_config, tmp_path
    ):
        """When Codex exits because its context window is full, resuming the
        same session just repeats the failure. The watchdog must clear the
        cached session before respawn so SpawnCoordinator starts fresh.
        """
        observed_uuid = "88888888-8888-8888-8888-888888888888"
        rollout = (
            state_dir / "workdirs" / "review" / "codex-home" / "sessions"
            / "2026" / "04" / "15"
            / f"rollout-2026-04-15T00-00-00-{observed_uuid}.jsonl"
        )
        rollout.parent.mkdir(parents=True)
        rollout.write_text("{}\n")

        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        registry.bind_codex_session("review", observed_uuid, session_path=rollout)
        registry.mark_spawned("review")

        transport = _ContextOverflowTransport()
        transport.alive_flags["review"] = True
        transport.spawn_calls.append(("review", []))
        orch = Orchestrator(state_dir, codex_review_config, transport)

        transport.alive_flags["review"] = False
        for _ in range(3):
            orch.run_once()

        respawn_calls = [
            c for c in transport.spawn_calls[1:] if c[0] == "review"
        ]
        assert respawn_calls
        _, argv = respawn_calls[0]
        assert "resume" not in argv
        assert observed_uuid not in argv

        fresh_registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        assert fresh_registry.get("review") is None
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(
            e.type == "worker.context.critical"
            and e.actor == "review"
            and e.payload.get("reason") == "provider_context_window_exhausted"
            for e in events
        )
