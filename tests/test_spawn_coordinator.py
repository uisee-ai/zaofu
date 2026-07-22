"""Tests for G-RESUME-3: SpawnCoordinator — unified spawn flow.

The coordinator owns the glue between BackendAdapter, RoleSessionRegistry,
and TransportAdapter so the spawn path is consistent whether called by
start.py (first boot) or by Orchestrator._respawn_instance (after crash).

Claude: pre-seed session_id via uuid5, use --session-id on first spawn,
        --resume on restart.
Codex:  interactive TUI in tmux pane (same shape as Claude). First
        spawn runs ``codex`` (no resume); restart uses
        ``codex resume <cached_uuid>`` with uuid observed from the
        session file written after the first turn. ``--last`` is
        intentionally never used — Strategy B (fresh start + warning
        event) covers the "uuid never observed before crash" case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ConstraintsConfig,
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.spawn_coordinator import SpawnCoordinator
from zf.runtime.spawn_coordinator import _write_codex_runtime_hook_trust
from zf.runtime.spawn_coordinator import _uuid_used_by_live_process
from zf.runtime.transport import TransportAdapter, AttachHandle


class _RecordingTransport(TransportAdapter):
    """Captures spawn/send_task/terminate calls for assertions."""

    def __init__(self):
        self.spawn_calls: list[tuple[str, list[str], Path | None]] = []
        self.send_task_calls: list[tuple[str, str]] = []
        self.terminate_calls: list[str] = []
        self.alive: dict[str, bool] = {}

    def init(self): pass
    def is_session_running(self): return True

    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd: Path | None = None,
    ) -> None:
        self.spawn_calls.append((role.instance_id, argv, cwd))
        self.alive[role.instance_id] = True

    def is_alive(self, role_name: str) -> bool:
        return self.alive.get(role_name, False)

    def wait_ready(self, role_name, pattern, timeout): return True

    def send_task(self, role_name, briefing_path, prompt):
        self.send_task_calls.append((role_name, prompt))

    def capture_log(self, role_name, lines=200): return ""
    def poll_events(self): return []
    def attach_handle(self, role_name): return AttachHandle()

    def terminate(self, role_name):
        self.terminate_calls.append(role_name)
        self.alive[role_name] = False

    def shutdown(self): pass


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    return sd


@pytest.fixture
def registry(state_dir: Path):
    return RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root="/tmp/zf")


@pytest.fixture
def transport():
    return _RecordingTransport()


@pytest.fixture
def coordinator(state_dir, registry, transport):
    return SpawnCoordinator(
        state_dir=state_dir,
        registry=registry,
        transport=transport,
        project_root="/tmp/zf",
    )


class TestClaudeFirstSpawn:
    def test_first_spawn_uses_session_id_flag(self, coordinator, transport, registry):
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        coordinator.spawn(role)
        assert len(transport.spawn_calls) == 1
        inst_id, argv, _ = transport.spawn_calls[0]
        assert inst_id == "dev"
        assert "--session-id" in argv
        # The uuid should come from registry.get_or_create("dev")
        expected = str(registry.get("dev"))
        assert expected in argv
        # Not a resume on first spawn
        assert "--resume" not in argv

    def test_spawn_passes_worktree_cwd_to_transport(
        self, coordinator, transport, tmp_path
    ):
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        worktree = tmp_path / ".zf" / "workdirs" / "dev" / "project"

        coordinator.spawn(role, cwd=worktree)

        _, _, cwd = transport.spawn_calls[0]
        assert cwd == worktree


class TestClaudeRespawn:
    def test_respawn_uses_resume_flag(
        self, coordinator, transport, registry, monkeypatch,
    ):
        """B-W5-01 fix (2026-04-20): respawn requires both meta_spawned
        AND the claude session JSONL file existing. Stub the file probe
        to return True (simulates "pane crashed, session file survives")."""
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        coordinator.spawn(role)   # first → --session-id
        transport.spawn_calls.clear()

        # Simulate pane crash recovery: meta says spawned_at exists
        # (it does after first spawn) AND session file is still on disk.
        monkeypatch.setattr(
            coordinator, "_claude_session_exists", lambda _sid: True,
        )
        coordinator.spawn(role)   # second (true respawn)

        assert len(transport.spawn_calls) == 1
        _, argv, _ = transport.spawn_calls[0]
        assert "--resume" in argv
        assert "--session-id" not in argv
        # Same uuid reused
        assert str(registry.get("dev")) in argv

    def test_launch_event_records_attempt_and_resume(
        self, state_dir, registry, transport, monkeypatch,
    ):
        event_log = EventLog(state_dir / "events.jsonl")
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root="/tmp/zf",
            event_log=event_log,
        )
        role = RoleConfig(
            name="dev", backend="claude-code", permission_mode="bypass",
        )

        coordinator.spawn(role)
        monkeypatch.setattr(
            coordinator, "_claude_session_exists", lambda _sid: True,
        )
        coordinator.spawn(role)

        launches = [
            event for event in event_log.read_all()
            if event.type == "worker.launch_artifact.written"
        ]
        assert [event.payload["launch_attempt"] for event in launches] == [1, 2]
        assert [event.payload["is_resume"] for event in launches] == [False, True]

    def test_fresh_spawn_rotates_session_id_when_live_process_holds_it(
        self, coordinator, transport, registry, monkeypatch,
    ):
        # DID-6 (2026-06-19 e2e): a fresh --session-id spawn whose deterministic
        # id is still held by a live process (a prior dispatch of this instance
        # mid-teardown) must rotate to a fresh id rather than colliding with
        # "Session ID already in use".
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        coordinator.spawn(role)
        first_id = str(registry.get("dev"))
        transport.spawn_calls.clear()

        # Re-dispatch: not a resume (session file gone), but the old process
        # still holds first_id.
        monkeypatch.setattr(coordinator, "_claude_session_exists", lambda _sid: False)
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator._uuid_used_by_live_process",
            lambda uuid: uuid == first_id,
        )
        coordinator.spawn(role)

        _, argv, _ = transport.spawn_calls[0]
        assert "--session-id" in argv
        new_id = str(registry.get("dev"))
        assert new_id != first_id      # rotated to a fresh id
        assert new_id in argv
        assert first_id not in argv    # did not collide on the live-held id

    def test_rotation_purges_stale_lock_for_the_rotated_candidate(
        self, coordinator, transport, registry, tmp_path, monkeypatch,
    ):
        """A prior state_dir can have left the first rotated UUID stale.

        The old one-shot rotation only purged the original UUID.  The fresh
        candidate then inherited a second stale ``lastSessionId`` and Claude
        exited immediately with "Session ID is already in use".
        """
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator.Path.home",
            staticmethod(lambda: tmp_path),
        )
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        coordinator.spawn(role)
        first_id = str(registry.get("dev"))
        transport.spawn_calls.clear()

        probe = RoleSessionRegistry(tmp_path / "probe.yaml", project_root="/tmp/zf")
        probe.get_or_create("dev", backend="claude-code")
        rotated_id = str(probe.rotate("dev"))
        claude_config = tmp_path / ".claude.json"
        claude_config.write_text(json.dumps({
            "projects": {"/previous-run": {"lastSessionId": rotated_id}},
        }), encoding="utf-8")

        monkeypatch.setattr(coordinator, "_claude_session_exists", lambda _sid: False)
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator._uuid_used_by_live_process",
            lambda candidate: candidate == first_id,
        )

        coordinator.spawn(role)

        _, argv, _ = transport.spawn_calls[0]
        assert str(registry.get("dev")) == rotated_id
        assert rotated_id in argv
        assert json.loads(claude_config.read_text(encoding="utf-8"))["projects"]["/previous-run"]["lastSessionId"] == ""

    def test_second_spawn_without_session_file_uses_session_id(
        self, coordinator, transport, registry, monkeypatch,
    ):
        """B-W5-01 scenario: ``zf start --foreground`` invoked twice,
        meta has spawned_at from prior boot but the claude process exited
        without writing a session file. Must NOT pass --resume (claude
        would abort with 'No conversation found')."""
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        coordinator.spawn(role)
        transport.spawn_calls.clear()

        # File probe returns False — no session file on disk.
        monkeypatch.setattr(
            coordinator, "_claude_session_exists", lambda _sid: False,
        )
        coordinator.spawn(role)

        _, argv, _ = transport.spawn_calls[0]
        # Must be treated as first-spawn again, not resume.
        assert "--resume" not in argv
        assert "--session-id" in argv


class TestCodexFirstSpawn:
    def test_codex_first_spawn_calls_transport_spawn_with_interactive_argv(
        self, coordinator, transport
    ):
        """Codex now runs as a persistent TUI in a tmux pane — same
        shape as Claude. First spawn calls transport.spawn with
        ``codex --dangerously-... [--model X]`` (no exec, no resume)."""
        role = RoleConfig(name="dev", backend="codex")
        coordinator.spawn(role)
        assert len(transport.spawn_calls) == 1
        inst_id, argv, _ = transport.spawn_calls[0]
        assert inst_id == "dev"
        assert argv[0] == "env"
        assert f"ZF_PROJECT_ROOT={Path('/tmp/zf').resolve()}" in argv
        assert f"ZF_STATE_DIR={coordinator.state_dir.resolve()}" in argv
        codex_home_arg = next(arg for arg in argv if arg.startswith("CODEX_HOME="))
        assert "codex" in argv
        assert "exec" not in argv
        assert "resume" not in argv
        assert "--dangerously-bypass-approvals-and-sandbox" in argv
        codex_home = Path(codex_home_arg.split("=", 1)[1])
        assert codex_home.name == "codex-home"

    def test_spawn_injects_runtime_env_for_claude(self, coordinator, transport):
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")

        coordinator.spawn(role)

        _, argv, _ = transport.spawn_calls[0]
        assert argv[0] == "env"
        assert f"ZF_PROJECT_ROOT={Path('/tmp/zf').resolve()}" in argv
        assert f"ZF_STATE_DIR={coordinator.state_dir.resolve()}" in argv
        assert "claude" in argv

    def test_codex_home_does_not_copy_project_hooks(
        self, state_dir, registry, transport, tmp_path,
    ):
        project_root = tmp_path / "project"
        (project_root / ".codex").mkdir(parents=True)
        (project_root / ".codex" / "hooks.json").write_text("{}")
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root=str(project_root),
        )

        coordinator.spawn(RoleConfig(name="dev", backend="codex"))

        codex_home = state_dir / "workdirs" / "dev" / "codex-home"
        assert not (codex_home / "hooks.json").exists()

    def test_codex_home_uses_role_local_sessions_dir(
        self, state_dir, registry, transport, tmp_path, monkeypatch,
    ):
        home = tmp_path / "home"
        global_sessions = home / ".codex" / "sessions"
        global_sessions.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root=str(tmp_path),
        )

        coordinator.spawn(RoleConfig(name="dev", backend="codex"))

        codex_home = state_dir / "workdirs" / "dev" / "codex-home"
        sessions = codex_home / "sessions"
        assert sessions.exists()
        assert sessions.is_dir()
        assert not sessions.is_symlink()

    def test_codex_home_records_project_hook_trust_state(
        self, state_dir, tmp_path,
    ):
        config = state_dir / "workdirs" / "dev" / "codex-home" / "config.toml"
        project_root = tmp_path / "project"
        hook_key = f"{project_root}/.codex/hooks.json:stop:0:0"

        _write_codex_runtime_hook_trust(
            config,
            project_root=project_root,
            hook_states=[(hook_key, "sha256:abc")],
        )

        text = config.read_text()
        assert f'[projects."{project_root}"]' in text
        assert 'trust_level = "trusted"' in text
        assert f'[hooks.state."{hook_key}"]' in text
        assert 'trusted_hash = "sha256:abc"' in text

    def test_codex_spawn_trusts_workdir_project_hooks(
        self, state_dir, registry, transport, tmp_path, monkeypatch,
    ):
        """Codex must trust hooks from the actual spawn cwd worktree.

        In worktree mode Codex loads ``<cwd>/.codex/hooks.json``. Trusting
        only the source project root leaves each role pane blocked at the
        interactive ``/hooks`` review prompt.
        """
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        source_root = tmp_path / "source"
        workdir_project = tmp_path / ".zf" / "workdirs" / "dev" / "project"
        (source_root / ".codex").mkdir(parents=True)
        (workdir_project / ".codex").mkdir(parents=True)
        (source_root / ".codex" / "hooks.json").write_text(
            "old source root hook",
            encoding="utf-8",
        )
        (workdir_project / ".codex" / "hooks.json").write_text(
            "zf hook-recv --state-dir /old/project/.zf",
            encoding="utf-8",
        )

        # Hook trust is now computed deterministically (codex_hook_hash),
        # not fetched via the codex 0.133 app-server RPC, so no mock needed.
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root=str(source_root),
        )

        coordinator.spawn(
            RoleConfig(name="dev", backend="codex"),
            cwd=workdir_project,
        )

        workdir_hooks = workdir_project / ".codex" / "hooks.json"
        hook_text = workdir_hooks.read_text(encoding="utf-8")
        assert f"--state-dir {state_dir}" in hook_text
        assert "/old/project/.zf" not in hook_text

        config = state_dir / "workdirs" / "dev" / "codex-home" / "config.toml"
        config_text = config.read_text(encoding="utf-8")
        assert f'[projects."{workdir_project.resolve()}"]' in config_text
        assert (
            f'[hooks.state."{workdir_project.resolve()}/.codex/hooks.json:stop:0:0"]'
            in config_text
        )
        from zf.runtime.codex_hooks import codex_hook_hash
        expected_stop_hash = codex_hook_hash(state_dir, "Stop", "codex.hook.stop")
        assert f'trusted_hash = "{expected_stop_hash}"' in config_text
        assert f'[projects."{source_root.resolve()}"]' not in config_text

    def test_fanout_synth_spawn_uses_pure_aggregator_policy(
        self,
        state_dir,
        registry,
        transport,
        tmp_path,
    ):
        log = EventLog(state_dir / "events.jsonl")
        role = RoleConfig(
            name="review-synth",
            backend="codex",
            role_kind="reader",
            permission_mode="bypass",
            allowed_tools=["Read"],
            constraints=ConstraintsConfig(allowed_paths=["src"]),
        )
        config = ZfConfig(
            project=ProjectConfig(name="synth-policy"),
            roles=[role],
            workflow=WorkflowConfig(stages=[
                WorkflowStageConfig(
                    id="review",
                    topology="fanout_reader",
                    aggregate=FanoutAggregateConfig(synth_role="review-synth"),
                ),
            ]),
        )
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root=str(tmp_path),
            event_log=log,
            config=config,
        )

        coordinator.spawn(role)

        _, argv, _ = transport.spawn_calls[0]
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
        assert "-a" in argv
        assert argv[argv.index("-a") + 1] == "untrusted"
        assert "-s" in argv
        assert argv[argv.index("-s") + 1] == "read-only"
        assert "--add-dir" not in argv
        applied = [
            event for event in log.read_all()
            if event.type == "worker.policy.applied"
        ][0]
        assert applied.payload["policy_id"] == "pure_aggregator.v1"
        assert applied.payload["changes"]["permission_mode"]["to"] == "restricted"
        assert applied.payload["changes"]["allowed_tools"]["to"] == []
        assert applied.payload["changes"]["constraints.allowed_paths"]["to"] == []


class TestCodexRespawnWithCachedUuid:
    def test_codex_respawn_uses_cached_session_id(
        self, coordinator, transport, registry, tmp_path, monkeypatch
    ):
        # Prime the registry with a fake observed codex session
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        u = "55555555-5555-5555-5555-555555555555"
        folder = fake_codex / "sessions" / "2026" / "04" / "15"
        folder.mkdir(parents=True)
        (folder / f"rollout-2026-04-15T00-00-00-{u}.jsonl").write_text("{}")
        registry.observe_codex_session("dev", since_ts=0)

        role = RoleConfig(name="dev", backend="codex")
        # Simulate first spawn already happened: mark as spawned
        registry.mark_spawned("dev")

        transport.spawn_calls.clear()
        coordinator.spawn(role)  # respawn

        assert len(transport.spawn_calls) == 1
        _, argv, _ = transport.spawn_calls[0]
        assert "resume" in argv
        resume_idx = argv.index("resume")
        assert argv[resume_idx + 1] == u

    def test_codex_respawn_with_missing_cached_session_starts_fresh(
        self, state_dir, registry, transport, tmp_path
    ):
        from zf.core.events.log import EventLog

        fake_codex = tmp_path / "codex_home"
        sessions = fake_codex / "sessions" / "2026" / "04" / "15"
        sessions.mkdir(parents=True)
        u = "66666666-6666-6666-6666-666666666666"
        rollout = sessions / f"rollout-2026-04-15T00-00-00-{u}.jsonl"
        rollout.write_text("{}")
        registry.observe_codex_session(
            "dev",
            since_ts=0,
            sessions_root=fake_codex / "sessions",
        )
        rollout.unlink()
        registry.mark_spawned("dev")
        event_log = EventLog(tmp_path / "events.jsonl")
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root="/tmp/zf",
            event_log=event_log,
        )

        coordinator.spawn(RoleConfig(name="dev", backend="codex"))

        _, argv, _ = transport.spawn_calls[0]
        assert "resume" not in argv
        assert registry.get("dev") is None
        warns = [e for e in event_log.read_all()
                 if e.type == "worker.spawn_warning"]
        assert warns[-1].payload["code"] == "codex_cached_session_missing"


class TestCodexRespawnWithoutCachedUuid:
    """Strategy B: codex died before observe_codex_session captured the
    uuid → respawn produces a fresh codex (no resume) and emits a warning
    event. NEVER fall back to ``--last`` (multi-instance footgun)."""

    def test_codex_respawn_without_uuid_starts_fresh_no_resume(
        self, state_dir, registry, transport
    ):
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root="/tmp/zf",
        )
        role = RoleConfig(name="dev", backend="codex")
        registry.mark_spawned("dev")  # simulate prior spawn that crashed before observe

        coordinator.spawn(role)
        assert len(transport.spawn_calls) == 1
        _, argv, _ = transport.spawn_calls[0]
        assert "resume" not in argv
        assert "--last" not in argv

    def test_codex_respawn_without_uuid_emits_warning_event(
        self, state_dir, registry, transport, tmp_path
    ):
        from zf.core.events.log import EventLog

        event_log = EventLog(tmp_path / "events.jsonl")
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root="/tmp/zf",
            event_log=event_log,
        )
        role = RoleConfig(name="dev", backend="codex")
        registry.mark_spawned("dev")

        coordinator.spawn(role)
        events = event_log.read_all()
        warns = [e for e in events if e.type == "worker.spawn_warning"]
        assert len(warns) == 1
        assert warns[0].payload["code"] == "codex_no_cached_session"
        assert warns[0].payload["instance_id"] == "dev"


class TestCodexUpdatePromptDismissal:
    """Codex shows a blocking 'Update available! Press enter' prompt at
    TUI start when latest_version != dismissed_version. SpawnCoordinator
    rewrites ~/.codex/version.json before spawning to convert it to a
    non-blocking banner."""

    def test_spawn_dismisses_update_prompt(
        self, coordinator, transport, monkeypatch, tmp_path
    ):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        codex_dir = fake_home / ".codex"
        codex_dir.mkdir()
        version_file = codex_dir / "version.json"
        version_file.write_text(
            '{"latest_version":"0.121.0","last_checked_at":"x","dismissed_version":null}'
        )
        monkeypatch.setattr("os.path.expanduser", lambda p: str(fake_home / p[2:]))

        role = RoleConfig(name="dev", backend="codex")
        coordinator.spawn(role)

        import json
        data = json.loads(version_file.read_text())
        assert data["dismissed_version"] == "0.121.0"

    def test_spawn_skips_when_already_dismissed(
        self, coordinator, transport, monkeypatch, tmp_path
    ):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        codex_dir = fake_home / ".codex"
        codex_dir.mkdir()
        version_file = codex_dir / "version.json"
        version_file.write_text(
            '{"latest_version":"0.121.0","last_checked_at":"x","dismissed_version":"0.121.0"}'
        )
        original_mtime = version_file.stat().st_mtime
        monkeypatch.setattr("os.path.expanduser", lambda p: str(fake_home / p[2:]))

        import time as _t
        _t.sleep(0.01)  # ensure mtime resolution
        role = RoleConfig(name="dev", backend="codex")
        coordinator.spawn(role)

        # File should not have been rewritten
        assert version_file.stat().st_mtime == original_mtime

    def test_spawn_handles_missing_version_file_gracefully(
        self, coordinator, transport, monkeypatch, tmp_path
    ):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        # No .codex/version.json at all
        monkeypatch.setattr("os.path.expanduser", lambda p: str(fake_home / p[2:]))

        role = RoleConfig(name="dev", backend="codex")
        coordinator.spawn(role)  # should not raise

    def test_non_codex_role_does_not_touch_version_file(
        self, coordinator, transport, monkeypatch, tmp_path
    ):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        codex_dir = fake_home / ".codex"
        codex_dir.mkdir()
        version_file = codex_dir / "version.json"
        version_file.write_text(
            '{"latest_version":"0.121.0","last_checked_at":"x","dismissed_version":null}'
        )
        monkeypatch.setattr("os.path.expanduser", lambda p: str(fake_home / p[2:]))

        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        coordinator.spawn(role)

        import json
        # Untouched
        data = json.loads(version_file.read_text())
        assert data["dismissed_version"] is None


class TestNotifyFirstDispatch:
    """Codex hook: after a successful send_task, the orchestrator calls
    notify_first_dispatch which schedules a background observation of
    the codex session file (codex writes it only after the first turn)."""

    def test_non_codex_role_is_noop(self, coordinator, registry):
        role = RoleConfig(name="dev", backend="claude-code")
        # Should not raise, should not touch any state we can observe.
        coordinator.notify_first_dispatch(role)
        assert "dev" not in coordinator._codex_observe_inflight

    def test_codex_with_cached_uuid_is_noop(
        self, coordinator, registry, tmp_path, monkeypatch
    ):
        # Prime registry with cached uuid
        fake_codex = tmp_path / "codex_home"
        fake_codex.mkdir()
        monkeypatch.setattr(
            "zf.core.state.role_sessions.CODEX_SESSIONS_ROOT",
            fake_codex / "sessions",
        )
        u = "88888888-8888-8888-8888-888888888888"
        folder = fake_codex / "sessions" / "2026" / "04" / "15"
        folder.mkdir(parents=True)
        (folder / f"rollout-2026-04-15T00-00-00-{u}.jsonl").write_text("{}")
        registry.observe_codex_session("dev", since_ts=0)
        assert registry.get("dev") is not None  # precondition

        role = RoleConfig(name="dev", backend="codex")
        coordinator.notify_first_dispatch(role)
        # Already cached → no background thread launched
        assert "dev" not in coordinator._codex_observe_inflight

    def test_codex_without_cached_uuid_schedules_observation(
        self, coordinator, registry, transport
    ):
        import time as _time
        role = RoleConfig(name="dev", backend="codex")
        coordinator.spawn(role)  # records spawn_ts

        # No file yet; create one shortly after to simulate codex
        # writing it after the first turn starts.
        u = "99999999-9999-9999-9999-999999999999"
        folder = coordinator._codex_sessions_root(role) / "2026" / "04" / "15"
        folder.mkdir(parents=True, exist_ok=True)

        coordinator.notify_first_dispatch(role)
        # File appears after a short delay
        _time.sleep(0.3)
        (folder / f"rollout-2026-04-15T00-00-00-{u}.jsonl").write_text("{}")

        # Wait for background observe to pick it up
        for _ in range(50):
            if registry.get("dev") is not None:
                break
            _time.sleep(0.2)
        assert str(registry.get("dev")) == u

    def test_background_observe_uses_role_local_sessions_root(
        self, coordinator, registry, monkeypatch
    ):
        captured = {}

        def fake_observe(instance_id, **kwargs):
            captured["instance_id"] = instance_id
            captured.update(kwargs)
            return None

        monkeypatch.setattr(registry, "observe_codex_session", fake_observe)
        role = RoleConfig(name="dev", backend="codex")

        coordinator._observe_codex_in_background(role, since_ts=123.0)

        assert captured["instance_id"] == "dev"
        assert captured["since_ts"] == 123.0
        assert captured["sessions_root"] == coordinator._codex_sessions_root(role)


class TestMarkSpawnedSemantics:
    def test_first_spawn_marks_and_returns_initial_command(
        self, coordinator, transport, registry
    ):
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        coordinator.spawn(role)
        # After spawn, registry knows this instance was spawned
        assert registry.mark_spawned("dev") is True  # already marked

    def test_fresh_registry_mark_spawned_is_false(self, registry):
        assert registry.mark_spawned("ghost") is False


# ---------------------------------------------------------------------------
# P0 backlog 2026-05-14-1549 — purge stale claude session before spawn
# ---------------------------------------------------------------------------


class TestPurgeStaleClaudeSession:
    def test_purge_clears_lastSessionId_when_no_live_process(
        self, state_dir, registry, transport, tmp_path, monkeypatch,
    ):
        """The real lock (per backlog 2026-05-14-1549): when ~/.claude.json
        has lastSessionId == uuid for any project entry, claude rejects
        --session-id <uuid> on next launch. spawn() must clear it."""
        # Pretend HOME is tmp_path so we don't touch the real ~/.claude.json
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator.Path.home",
            staticmethod(lambda: tmp_path),
        )
        # No live process holds the uuid
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator._uuid_used_by_live_process",
            lambda uuid: False,
        )

        events_path = state_dir / "events.jsonl"
        event_log = EventLog(events_path)

        coord = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root="/tmp/zf",
            event_log=event_log,
        )
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        # Determine the uuid that registry will assign
        expected_uuid = str(registry.get_or_create("dev", backend="claude-code"))

        # Seed ~/.claude.json with a stale lastSessionId
        claude_config = tmp_path / ".claude.json"
        claude_config.write_text(json.dumps({
            "projects": {
                "/some/repo": {
                    "lastSessionId": expected_uuid,
                    "lastHintSessionId": expected_uuid,
                    "hasTrustDialogAccepted": True,
                },
            },
        }))

        coord.spawn(role)

        # After spawn, claude.json should have been cleared
        post = json.loads(claude_config.read_text())
        proj = post["projects"]["/some/repo"]
        assert proj["lastSessionId"] == ""
        assert proj["lastHintSessionId"] == ""

        # And a purge event should have been emitted
        events = [
            json.loads(ln)
            for ln in events_path.read_text().splitlines()
            if ln.strip()
        ]
        purged = [e for e in events if e["type"] == "worker.spawn.stale_session_purged"]
        assert len(purged) == 1
        assert purged[0]["payload"]["session_id"] == expected_uuid
        assert any(
            "lastSessionId" in f
            for f in purged[0]["payload"]["claude_json_fields_cleared"]
        )

    def test_purge_skipped_when_live_process_holds_uuid(
        self, state_dir, registry, transport, tmp_path, monkeypatch,
    ):
        """Safety: don't yank state from a running process."""
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator.Path.home",
            staticmethod(lambda: tmp_path),
        )
        events_path = state_dir / "events.jsonl"
        event_log = EventLog(events_path)
        coord = SpawnCoordinator(
            state_dir=state_dir, registry=registry, transport=transport,
            project_root="/tmp/zf", event_log=event_log,
        )
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        expected_uuid = str(registry.get_or_create("dev", backend="claude-code"))

        # The old UUID is owned by a live process. A rotated UUID is available,
        # so the coordinator must leave the live session's lock untouched and
        # continue with the new candidate.
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator._uuid_used_by_live_process",
            lambda uuid: uuid == expected_uuid,
        )

        claude_config = tmp_path / ".claude.json"
        claude_config.write_text(json.dumps({
            "projects": {
                "/some/repo": {"lastSessionId": expected_uuid},
            },
        }))

        coord.spawn(role)

        # claude.json should NOT have been touched (lastSessionId stays)
        post = json.loads(claude_config.read_text())
        assert post["projects"]["/some/repo"]["lastSessionId"] == expected_uuid
        assert str(registry.get("dev")) != expected_uuid

        # No purge event
        if events_path.exists():
            events = [
                json.loads(ln)
                for ln in events_path.read_text().splitlines()
                if ln.strip()
            ]
            assert not any(
                e["type"] == "worker.spawn.stale_session_purged" for e in events
            )

    def test_purge_skipped_on_respawn(
        self, state_dir, registry, transport, tmp_path, monkeypatch,
    ):
        """Respawn passes --resume <uuid>, not --session-id. The
        lastSessionId reference is exactly what --resume wants."""
        monkeypatch.setattr(
            "zf.runtime.spawn_coordinator.Path.home",
            staticmethod(lambda: tmp_path),
        )
        purge_calls = []

        def _track_purge(self, role, session_id):
            purge_calls.append(session_id)

        monkeypatch.setattr(
            SpawnCoordinator, "_purge_stale_claude_session", _track_purge,
        )

        coord = SpawnCoordinator(
            state_dir=state_dir, registry=registry, transport=transport,
            project_root="/tmp/zf",
        )
        role = RoleConfig(name="dev", backend="claude-code", permission_mode="bypass")
        # First spawn → purge runs
        coord.spawn(role)
        assert len(purge_calls) == 1

        # Second spawn (registry now marked, and claude session "exists")
        monkeypatch.setattr(coord, "_claude_session_exists", lambda sid: True)
        coord.spawn(role)
        # is_respawn=True so purge must NOT run again
        assert len(purge_calls) == 1

    def test_uuid_used_by_live_process_negative(self, monkeypatch):
        """No live process matches → False."""
        class _FakeProc:
            stdout = "init\nsshd\n--some-other-flag\n"

        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: _FakeProc(),
        )
        assert _uuid_used_by_live_process("11111111-1111-1111-1111-111111111111") is False

    def test_uuid_used_by_live_process_positive(self, monkeypatch):
        class _FakeProc:
            stdout = "claude --session-id 22222222-2222-2222-2222-222222222222 --verbose\n"

        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: _FakeProc(),
        )
        assert _uuid_used_by_live_process(
            "22222222-2222-2222-2222-222222222222"
        ) is True

    def test_uuid_used_by_live_process_fail_closed(self, monkeypatch):
        """If ps fails for any reason, return True (don't purge)."""
        def _raise(*a, **kw):
            raise OSError("ps unavailable")

        monkeypatch.setattr("subprocess.run", _raise)
        assert _uuid_used_by_live_process(
            "33333333-3333-3333-3333-333333333333"
        ) is True
