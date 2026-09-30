from __future__ import annotations

import shlex

import pytest

from zf.core.config.schema import RoleConfig
from zf.runtime.tmux import TmuxError
from zf.runtime.transport import (
    DispatchContext,
    TmuxTransport,
    transport_error_diagnostics,
    transport_readiness_error,
)


class _FakeTmux:
    dry_run = False

    def __init__(
        self,
        *,
        pid: str = "1234",
        command: str = "codex",
        path: str = "/repo",
        pane_alive: bool = True,
        process_probe: dict | None = None,
    ) -> None:
        self._pid = pid
        self._command = command
        self._path = path
        self._pane_alive = pane_alive
        self._process_probe = process_probe
        self.sent: list[tuple[str, str]] = []
        self.terminate_window_calls: list[str] = []
        self.kill_window_calls: list[str] = []

    def pane_pid(self, role_name: str) -> str:
        return self._pid

    def pane_alive(self, role_name: str) -> bool:
        return self._pane_alive

    def pane_current_command(self, role_name: str) -> str:
        return self._command

    def pane_current_path(self, role_name: str) -> str:
        return self._path

    def capture_pane(self, role_name: str, *, lines: int = 120) -> str:
        return ""

    def wait_for_prompt(self, role_name: str, pattern: str, *, timeout: float) -> bool:
        return True

    def pane_process_probe(self, role_name: str) -> dict:
        if self._process_probe is not None:
            return self._process_probe
        return {
            "available": False,
            "pane_pid": self._pid,
            "current_command": self._command,
            "processes": [],
        }

    def send_keys(self, role_name: str, command: str) -> None:
        self.sent.append((role_name, command))

    def terminate_window(self, role_name: str) -> None:
        self.terminate_window_calls.append(role_name)

    def kill_window(self, role_name: str) -> None:
        self.kill_window_calls.append(role_name)


def test_tmux_transport_lifecycle_snapshot_records_process_context() -> None:
    transport = TmuxTransport(
        _FakeTmux(pid="4242", command="codex", path="/workspace/project")  # type: ignore[arg-type]
    )

    snapshot = transport.lifecycle_snapshot("dev-1")

    assert snapshot.role_name == "dev-1"
    assert snapshot.alive is True
    assert snapshot.pane_pid == "4242"
    assert snapshot.current_command == "codex"
    assert snapshot.current_path == "/workspace/project"
    assert snapshot.to_payload()["pane_pid"] == "4242"


def test_tmux_transport_lifecycle_snapshot_marks_shell_as_not_agent() -> None:
    transport = TmuxTransport(
        _FakeTmux(pid="4242", command="bash", path="/workspace/project")  # type: ignore[arg-type]
    )

    snapshot = transport.lifecycle_snapshot("dev-1")

    assert snapshot.alive is False
    assert snapshot.current_command == "bash"


def test_ready_deadline_during_liveness_check_keeps_timeout_diagnostics(monkeypatch) -> None:
    now = 0.0
    transport = TmuxTransport(_FakeTmux())
    monkeypatch.setattr("zf.runtime.tmux_readiness.time.monotonic", lambda: now)

    def slow_alive(_role):
        nonlocal now
        now = 2.0
        return True

    monkeypatch.setattr(transport, "_agent_process_alive", slow_alive)
    assert transport.wait_ready("dev-1", r"[›>]", timeout=1.0) is False
    diagnostics = transport.readiness_diagnostics("dev-1")
    assert diagnostics["failure_class"] == "provider_ready_pattern_timeout"
    assert diagnostics["effective_timeout_seconds"] == 1.0


def test_tmux_transport_wait_ready_rejects_shell_even_if_prompt_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale prompt must not turn a failed provider launch into ready."""
    monkeypatch.setattr(TmuxTransport, "_AGENT_LAUNCH_GRACE_SECONDS", 0.0)
    transport = TmuxTransport(
        _FakeTmux(pid="4242", command="bash", path="/workspace/project")  # type: ignore[arg-type]
    )

    assert transport.wait_ready("dev-1", r"[❯>]", timeout=1.0) is False


def test_tmux_transport_wait_ready_allows_brief_shell_to_provider_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BootingTmux(_FakeTmux):
        def __init__(self) -> None:
            super().__init__(pid="4242", command="bash", path="/workspace/project")
            self.command_reads = 0

        def pane_current_command(self, role_name: str) -> str:
            self.command_reads += 1
            return "bash" if self.command_reads == 1 else "claude"

    monkeypatch.setattr(TmuxTransport, "_READY_POLL_INTERVAL_SECONDS", 0.0)
    transport = TmuxTransport(_BootingTmux())  # type: ignore[arg-type]

    assert transport.wait_ready("dev-1", r"[❯>]", timeout=1.0) is True


def test_tmux_transport_wait_ready_rejects_claude_oauth_prompt() -> None:
    class _OAuthTmux(_FakeTmux):
        def capture_pane(self, role_name: str, *, lines: int = 120) -> str:
            return (
                "Browser didn't open? Use the url below\n"
                "Paste code here if prompted >\n"
                "OAuth error: Invalid code. Press Enter to retry."
            )

    transport = TmuxTransport(
        _OAuthTmux(
            pid="4242",
            command="claude",
            path="/workspace/project",
        )  # type: ignore[arg-type]
    )
    transport._launch_records["run-manager"] = {
        "backend": "claude-code",
        "command": "claude",
        "launch_attempts": 1,
    }

    assert transport.wait_ready(
        "run-manager",
        r"[❯>]",
        timeout=1.0,
    ) is False
    diagnostics = transport.readiness_diagnostics("run-manager")
    assert diagnostics["failure_class"] == "provider_auth_interactive"
    assert diagnostics["interactive_prompt_marker"] == "login_required"


def test_tmux_transport_send_task_allows_normal_node_tui(tmp_path) -> None:
    tmux = _FakeTmux(pid="4242", command="node", path="/workspace/project")
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    briefing = tmp_path / "briefing.md"
    briefing.write_text("brief", encoding="utf-8")

    transport.send_task("dev-1", briefing, "do work")

    assert tmux.sent == [("dev-1", "do work")]


def test_tmux_transport_send_task_allows_node_codex_wrapper(tmp_path) -> None:
    tmux = _FakeTmux(
        pid="4242",
        command="node",
        path="/workspace/project",
        process_probe={
            "available": True,
            "pane_pid": "4242",
            "current_command": "node",
            "processes": [
                {"pid": "4243", "ppid": "4242", "command": "/usr/bin/node /bin/codex resume abc"},
            ],
        },
    )
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    briefing = tmp_path / "briefing.md"
    briefing.write_text("brief", encoding="utf-8")

    transport.send_task(
        "dev-1",
        briefing,
        "do work",
        context=DispatchContext(backend="codex"),
    )

    assert tmux.sent == [("dev-1", "do work"), ("dev-1", "")]
    snapshot = transport.lifecycle_snapshot("dev-1").to_payload()
    assert snapshot["process_probe"]["agent_markers"] == ["codex"]


def test_tmux_transport_send_task_rejects_node_without_agent_probe(tmp_path) -> None:
    tmux = _FakeTmux(
        pid="4242",
        command="node",
        path="/workspace/project",
        process_probe={
            "available": True,
            "pane_pid": "4242",
            "current_command": "node",
            "processes": [
                {"pid": "4243", "ppid": "4242", "command": "/usr/bin/node server.js"},
            ],
        },
    )
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    briefing = tmp_path / "briefing.md"
    briefing.write_text("brief", encoding="utf-8")

    with pytest.raises(Exception) as exc:
        transport.send_task(
            "dev-1",
            briefing,
            "do work",
            context=DispatchContext(backend="codex"),
        )

    diagnostics = transport_error_diagnostics(exc.value)
    assert "reason=node_without_agent_wrapper" in str(exc.value)
    assert diagnostics["backend"] == "codex"
    assert diagnostics["current_command"] == "node"
    assert diagnostics["process_probe"]["processes"][0]["command"].endswith("server.js")


def test_tmux_transport_send_task_reports_context_exhausted(tmp_path) -> None:
    class _ContextExhaustedTmux(_FakeTmux):
        def capture_pane(self, role_name: str, *, lines: int = 120) -> str:
            return "Codex ran out of room in the model's context window."

    transport = TmuxTransport(
        _ContextExhaustedTmux(
            pid="4242", command="node", path="/workspace/project"
        )
    )  # type: ignore[arg-type]
    briefing = tmp_path / "briefing.md"
    briefing.write_text("brief", encoding="utf-8")

    with pytest.raises(TmuxError) as exc:
        transport.send_task("dev-1", briefing, "do work")

    assert "current_command=node" in str(exc.value)
    assert "reason=provider_context_exhausted" in str(exc.value)


def test_tmux_transport_terminate_uses_controlled_shutdown() -> None:
    tmux = _FakeTmux(pid="4242", command="codex", path="/workspace/project")
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]

    transport.terminate("dev-1")

    assert tmux.terminate_window_calls == ["dev-1"]
    assert tmux.kill_window_calls == []


class _SpawnLayout:
    def create_slot(self, tmux, role) -> None:  # noqa: ANN001
        return None

    def record_cwd(self, tmux, instance_id, expected) -> None:  # noqa: ANN001
        return None


class _HandshakeTmux(_FakeTmux):
    def __init__(self, *, execute_probe: bool = True) -> None:
        super().__init__(command="bash")
        self.layout = _SpawnLayout()
        self.execute_probe = execute_probe
        self.screen = ""

    def capture_pane(self, role_name: str, *, lines: int = 120) -> str:
        return self.screen

    def send_keys(
        self,
        role_name: str,
        command: str,
        *,
        submit_delay_s: float = 0.5,
    ) -> None:
        self.sent.append((role_name, command))
        if self.execute_probe and command.startswith("printf "):
            tokens = shlex.split(command)
            self.screen = "".join(tokens[2:])


def test_tmux_spawn_proves_shell_input_before_provider_argv() -> None:
    tmux = _HandshakeTmux()
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    role = RoleConfig(name="dev", backend="codex", instance_id="dev-1")

    transport.spawn(role, ["codex", "--enable", "hooks"])

    assert tmux.sent[0][1].startswith("printf ")
    assert tmux.sent[-1][0] == "dev-1"
    assert tmux.sent[-1][1].endswith("codex --enable hooks")


def test_tmux_ready_retries_one_lost_launch_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmux = _HandshakeTmux()
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    transport._launch_records["dev-1"] = {
        "backend": "codex",
        "command": "codex",
        "launch_attempts": 1,
    }
    monkeypatch.setattr(TmuxTransport, "_AGENT_LAUNCH_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(TmuxTransport, "_READY_POLL_INTERVAL_SECONDS", 0.0)

    def submit_pending_enter(
        role_name: str,
        command: str,
        *,
        submit_delay_s: float = 0.5,
    ) -> None:
        tmux.sent.append((role_name, command))
        if command == "":
            tmux._command = "codex"

    tmux.send_keys = submit_pending_enter  # type: ignore[method-assign]

    assert transport.wait_ready("dev-1", r"[›>]", timeout=240.0) is True
    assert tmux.sent == [("dev-1", "")]
    assert transport._launch_records["dev-1"]["launch_attempts"] == 2


def test_codex_ready_timeout_is_bounded_and_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmux = _HandshakeTmux()
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    transport._launch_records["dev-1"] = {
        "backend": "codex",
        "command": "codex",
        "launch_attempts": 2,
    }
    monkeypatch.setattr(TmuxTransport, "_CODEX_READY_TIMEOUT_CAP_SECONDS", 0.01)
    monkeypatch.setattr(TmuxTransport, "_READY_POLL_INTERVAL_SECONDS", 0.0)

    # Exercise the readiness-pattern timeout branch deterministically.
    monkeypatch.setattr(transport, "_agent_process_alive", lambda _role: True)
    monkeypatch.setattr(tmux, "wait_for_prompt", lambda *_args, **_kwargs: False)

    assert transport.wait_ready("dev-1", r"[›>]", timeout=240.0) is False
    error = transport_readiness_error(
        transport,
        "dev-1",
        backend="codex",
    )
    diagnostics = transport_error_diagnostics(error)

    assert diagnostics["failure_class"] == "provider_ready_pattern_timeout"
    assert diagnostics["pane_alive"] is True
    assert diagnostics["current_command"] == "bash"
    assert diagnostics["requested_timeout_seconds"] == 240.0
    assert diagnostics["effective_timeout_seconds"] == 0.01
    assert "process_probe" in diagnostics
    assert "last_screen_excerpt" in diagnostics


def test_shell_handshake_timeout_has_explicit_failure_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmux = _HandshakeTmux(execute_probe=False)
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    role = RoleConfig(name="dev", backend="codex", instance_id="dev-1")
    monkeypatch.setattr(TmuxTransport, "_SHELL_HANDSHAKE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(TmuxTransport, "_READY_POLL_INTERVAL_SECONDS", 0.0)

    with pytest.raises(TmuxError) as caught:
        transport.spawn(role, ["codex"])

    diagnostics = transport_error_diagnostics(caught.value)
    assert diagnostics["failure_class"] == "shell_handshake_timeout"
    assert diagnostics["pane_alive"] is True
    assert diagnostics["current_command"] == "bash"
