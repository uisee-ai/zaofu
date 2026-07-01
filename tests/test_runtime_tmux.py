"""Tests for tmux runtime module."""

from __future__ import annotations

from zf.runtime.tmux import TmuxSession


class TestTmuxSessionDryRun:
    """All tests use dry_run=True so no real tmux is needed."""

    def setup_method(self):
        self.tmux = TmuxSession(session_name="test-zf", dry_run=True)

    def test_create_session_records_command(self):
        self.tmux.create_session()
        assert len(self.tmux.command_log) >= 1
        cmd = self.tmux.command_log[0]
        assert "new-session" in cmd or "new" in cmd
        assert "test-zf" in cmd

    def test_kill_session_records_command(self):
        self.tmux.kill_session()
        assert any("kill-session" in cmd for cmd in self.tmux.command_log)

    def test_create_window_records_command(self):
        self.tmux.create_window("dev-1")
        assert any("new-window" in cmd for cmd in self.tmux.command_log)
        assert any("dev-1" in cmd for cmd in self.tmux.command_log)

    def test_kill_window_records_command(self):
        self.tmux.kill_window("dev-1")
        assert any("kill-window" in cmd for cmd in self.tmux.command_log)

    def test_terminate_window_records_term_kill_and_hard_kill(self, monkeypatch):
        monkeypatch.setattr(self.tmux, "pane_pid", lambda window: "4242")

        result = self.tmux.terminate_window("dev-1", grace_seconds=0)

        assert result.pane_pid == "4242"
        assert result.term_sent is True
        assert result.kill_sent is True
        assert result.hard_killed is True
        assert any("os.killpg(4242, SIGTERM)" in cmd for cmd in self.tmux.command_log)
        assert any("os.killpg(4242, SIGKILL)" in cmd for cmd in self.tmux.command_log)
        assert any("kill-window" in cmd for cmd in self.tmux.command_log)

    def test_terminate_window_uses_killpg_not_shell_kill(
        self, monkeypatch
    ):
        # Regression: a pane pid whose first digit is 1 (e.g. 1731963) made procps
        # `kill -TERM -1731963` parse as a `-1…` option bundle, collapsing to
        # kill(-1, SIG) and SIGTERMing every uid process — killed the whole
        # session (tmux + ssh + systemd --user manager) 4× before this fix. The
        # runtime now calls os.killpg directly and never shells out to `kill`.
        monkeypatch.setattr(self.tmux, "pane_pid", lambda window: "1731963")

        self.tmux.terminate_window("dev-1", grace_seconds=0)

        signal_cmds = [c for c in self.tmux.command_log if c.startswith("os.")]
        assert signal_cmds
        assert all(not cmd.startswith("kill ") for cmd in self.tmux.command_log)
        assert any("os.killpg(1731963, SIGTERM)" in c for c in signal_cmds)

    def test_terminate_window_skips_signal_without_valid_pid(self, monkeypatch):
        monkeypatch.setattr(self.tmux, "pane_pid", lambda window: "")

        result = self.tmux.terminate_window("dev-1", grace_seconds=0)

        assert result.term_sent is False
        assert result.kill_sent is False
        assert result.hard_killed is True
        assert not any("SIGTERM" in cmd for cmd in self.tmux.command_log)
        assert any("kill-window" in cmd for cmd in self.tmux.command_log)

    def test_send_keys_records_command(self):
        self.tmux.send_keys("dev-1", "echo hello")
        assert any("send-keys" in cmd for cmd in self.tmux.command_log)

    def test_capture_pane_returns_empty_in_dry_run(self):
        result = self.tmux.capture_pane("dev-1")
        assert isinstance(result, str)

    def test_pane_alive_returns_true_in_dry_run(self):
        assert self.tmux.pane_alive("dev-1") is True

    def test_pane_pid_returns_empty_in_dry_run(self):
        assert self.tmux.pane_pid("dev-1") == ""

    def test_has_session_returns_false_in_dry_run(self):
        assert self.tmux.has_session() is False

    def test_wait_for_prompt_returns_true_in_dry_run(self):
        assert self.tmux.wait_for_prompt("dev-1", ">", timeout=0.1) is True

    def test_command_log_accumulates(self):
        self.tmux.create_session()
        self.tmux.create_window("w1")
        self.tmux.send_keys("w1", "ls")
        assert len(self.tmux.command_log) >= 3

    def test_send_keys_target_includes_session(self):
        self.tmux.send_keys("dev-1", "echo hi")
        # target should be session:window format
        assert any("test-zf" in cmd for cmd in self.tmux.command_log)


class TestStripAnsi:
    def test_strips_color_codes(self):
        text = "\x1b[32mgreen\x1b[0m normal"
        result = TmuxSession.strip_ansi(text)
        assert result == "green normal"

    def test_strips_cursor_movement(self):
        text = "\x1b[2J\x1b[Htext"
        result = TmuxSession.strip_ansi(text)
        assert result == "text"

    def test_plain_text_unchanged(self):
        result = TmuxSession.strip_ansi("hello world")
        assert result == "hello world"

    def test_empty_string(self):
        assert TmuxSession.strip_ansi("") == ""


class TestTmuxSessionInit:
    def test_default_session_name(self):
        t = TmuxSession(dry_run=True)
        assert t.session_name == "zf"

    def test_custom_session_name(self):
        t = TmuxSession(session_name="my-session", dry_run=True)
        assert t.session_name == "my-session"

    def test_dry_run_flag(self):
        t = TmuxSession(dry_run=True)
        assert t.dry_run is True

    def test_command_log_starts_empty(self):
        t = TmuxSession(dry_run=True)
        assert t.command_log == []
