"""Tests for the submit_key parameter on TmuxSession.send_keys.

Background: mixed-e2e investigation surfaced timing between tmux
send-keys and the TUI's readiness. ``submit_key`` is retained as an
extension hook for future TUIs that might need a non-Enter submit,
but both Claude Code and Codex v0.120.0 accept the default ``Enter``.

History: the B-1203-04 fix attempted `C-m` for codex; a follow-up
smoke showed `Enter` actually works (the earlier C-m "success" was a
timing coincidence — codex had finally woken up from a slow boot).
Reverted to default; this file now just locks in the kwarg contract.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.runtime.tmux import TmuxError, TmuxSession
from zf.runtime.transport import TmuxTransport


def test_tmux_send_keys_accepts_submit_key_param():
    """send_keys keeps a submit_key kwarg for future backends."""
    sess = TmuxSession(session_name="zf-test", dry_run=True)
    sess.send_keys("role1", "hello", submit_key="C-m")
    log = sess.command_log
    assert any("C-m" in c for c in log), (
        f"expected C-m when explicitly requested, got: {log}"
    )


def test_tmux_send_keys_defaults_to_enter():
    """Default behavior: trailing ``Enter`` keystroke (B2 fix)."""
    sess = TmuxSession(session_name="zf-test", dry_run=True)
    sess.send_keys("role1", "hello")
    log = sess.command_log
    assert any("Enter" in c for c in log)


def test_send_task_uses_enter_for_all_backends(tmp_path: Path):
    """Both claude and codex submit via ``Enter``. Recorded here so a
    future "need C-m" regression can't silently re-land."""
    tmux = TmuxSession(session_name="zf-test", dry_run=True)
    transport = TmuxTransport(tmux)
    for backend in ("claude-code", "codex"):
        role = RoleConfig(name=f"{backend}-role", backend=backend)
        transport.spawn(role, [backend])
        tmux.command_log.clear()
        transport.send_task(role.instance_id, tmp_path / "brief.md", "task")
        submits = [c for c in tmux.command_log
                   if "send-keys" in c and c.endswith(" Enter")]
        assert submits, (
            f"{backend} send_task must submit via Enter, got: "
            f"{tmux.command_log}"
        )


def test_send_task_clears_unsubmitted_draft_before_prompt(tmp_path: Path):
    tmux = TmuxSession(session_name="zf-test", dry_run=True)
    transport = TmuxTransport(tmux)
    role = RoleConfig(name="planner", backend="claude-code")
    transport.spawn(role, ["claude"])
    tmux.command_log.clear()

    transport.send_task(role.instance_id, tmp_path / "brief.md", "new briefing")

    sends = [command for command in tmux.command_log if "send-keys" in command]
    clear_index = next(
        index for index, command in enumerate(sends) if command.endswith(" C-u")
    )
    prompt_index = next(
        index for index, command in enumerate(sends) if "new briefing" in command
    )
    assert clear_index < prompt_index


class _FakeTmux:
    dry_run = False

    def __init__(self, current_command: str, current_path: str = "") -> None:
        self.current_command = current_command
        self.current_path = current_path
        self.sent: list[str] = []

    def pane_alive(self, role_name: str) -> bool:
        return True

    def pane_current_command(self, role_name: str) -> str:
        return self.current_command

    def pane_current_path(self, role_name: str) -> str:
        return self.current_path

    def send_keys(self, role_name: str, prompt: str) -> None:
        self.sent.append(prompt)


class _FakeSpawnLayout:
    def create_slot(self, tmux, role) -> None:  # noqa: ANN001
        return None

    def record_cwd(self, tmux, instance_id: str, expected: Path) -> None:  # noqa: ANN001
        return None


class _FakeSpawnTmux:
    dry_run = False

    def __init__(self) -> None:
        self.layout = _FakeSpawnLayout()
        self.sent: list[tuple[str, str]] = []

    def send_keys(self, role_name: str, command: str) -> None:
        self.sent.append((role_name, command))


def test_spawn_carries_selected_runtime_env_into_agent(monkeypatch, tmp_path: Path):
    """Agent panes must inherit the active ZaoFu source path.

    A long-lived tmux server can have stale environment, so relying on tmux
    inheritance lets in-pane `zf` commands import an old installed package.
    """
    monkeypatch.setenv("PYTHONPATH", "/repo/zaofu/src")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    tmux = _FakeSpawnTmux()
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    role = RoleConfig(name="dev", backend="codex", instance_id="dev-1")

    transport.spawn(role, ["codex", "--enable", "hooks"], cwd=tmp_path)

    assert tmux.sent
    role_name, command = tmux.sent[-1]
    assert role_name == "dev-1"
    assert command.startswith(f"cd {tmp_path} && /usr/bin/env ")
    assert "PYTHONPATH=/repo/zaofu/src" in command
    assert "PATH=/usr/bin:/bin" in command
    assert " codex --enable hooks" in command


def test_send_task_refuses_shell_pane(tmp_path: Path):
    transport = TmuxTransport(_FakeTmux("bash"))  # type: ignore[arg-type]

    try:
        transport.send_task("dev", tmp_path / "brief.md", "task")
    except TmuxError as exc:
        assert "current_command=bash" in str(exc)
    else:
        raise AssertionError("expected TmuxError for shell pane")


def test_send_task_allows_agent_process(tmp_path: Path):
    tmux = _FakeTmux("codex")
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]

    transport.send_task("dev", tmp_path / "brief.md", "task")

    assert tmux.sent == ["task"]


def test_send_task_refuses_wrong_workdir(tmp_path: Path):
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    expected.mkdir()
    actual.mkdir()
    tmux = _FakeTmux("codex", current_path=str(actual))
    transport = TmuxTransport(tmux)  # type: ignore[arg-type]
    transport._expected_cwds["dev"] = expected.resolve()

    try:
        transport.send_task("dev", tmp_path / "brief.md", "task")
    except TmuxError as exc:
        assert "pane cwd mismatch" in str(exc)
        assert str(expected.resolve()) in str(exc)
        assert str(actual.resolve()) in str(exc)
    else:
        raise AssertionError("expected TmuxError for wrong pane cwd")
