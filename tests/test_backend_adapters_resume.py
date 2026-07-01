"""Tests for G-RESUME-1: BackendAdapter.build_command accepts session_id + is_resume.

New signature lets the SpawnCoordinator pre-seed the conversation for
Claude and switch to `exec resume` for Codex, so tmux-hosted agents
preserve conversation history across pane crashes and restarts.
"""

from __future__ import annotations

import pytest

from zf.core.config.schema import RoleConfig
from zf.runtime.backend import (
    BackendAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    MockAdapter,
    get_adapter,
)


UUID_A = "11111111-1111-5111-8111-111111111111"
UUID_B = "22222222-2222-5222-8222-222222222222"


class TestClaudeInitialCommand:
    def test_first_spawn_with_session_id_includes_session_id_flag(self):
        role = RoleConfig(name="dev", permission_mode="bypass")
        cmd = ClaudeCodeAdapter().build_command(
            role, session_id=UUID_A, is_resume=False,
        )
        assert "--session-id" in cmd
        assert UUID_A in cmd
        # Backward: dangerous-skip still present for bypass mode
        assert "--dangerously-skip-permissions" in cmd

    def test_first_spawn_no_session_id_omits_flag(self):
        role = RoleConfig(name="dev", permission_mode="bypass")
        cmd = ClaudeCodeAdapter().build_command(
            role, session_id=None, is_resume=False,
        )
        assert "--session-id" not in cmd
        assert "--resume" not in cmd

    def test_explicit_none_session_id_same_as_omitted(self):
        role = RoleConfig(name="dev")
        cmd = ClaudeCodeAdapter().build_command(role)  # all defaults
        assert "--session-id" not in cmd
        assert "--resume" not in cmd


class TestClaudeResumeCommand:
    def test_resume_true_with_session_id_uses_resume_flag(self):
        role = RoleConfig(name="dev", permission_mode="bypass")
        cmd = ClaudeCodeAdapter().build_command(
            role, session_id=UUID_A, is_resume=True,
        )
        assert "--resume" in cmd
        assert UUID_A in cmd
        # --session-id NOT used when resuming
        assert "--session-id" not in cmd

    def test_resume_without_session_id_is_error_free_noop(self):
        """is_resume=True but session_id=None should not raise; it just
        omits both flags (caller is responsible for logic)."""
        role = RoleConfig(name="dev")
        cmd = ClaudeCodeAdapter().build_command(
            role, session_id=None, is_resume=True,
        )
        assert "--resume" not in cmd


class TestCodexInitialCommand:
    def test_first_spawn_uses_interactive_tui_with_bypass_flag(self):
        role = RoleConfig(name="dev")
        cmd = CodexAdapter().build_command(
            role, session_id=None, is_resume=False, prompt="go",
        )
        assert cmd[0] == "codex"
        # Interactive TUI mode: no exec subcommand
        assert "exec" not in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        # prompt is delivered via tmux send-keys, not argv
        assert "go" not in cmd
        assert "resume" not in cmd

    def test_first_spawn_ignores_session_id_argument(self):
        """Codex can't pre-seed session_id; even if caller passes it,
        the first-spawn command must not try to use it."""
        role = RoleConfig(name="dev")
        cmd = CodexAdapter().build_command(
            role, session_id=UUID_A, is_resume=False, prompt="hi",
        )
        assert UUID_A not in cmd  # not a resume path
        assert "resume" not in cmd


class TestCodexResumeCommand:
    def test_resume_uses_resume_subcommand_with_uuid(self):
        role = RoleConfig(name="dev")
        cmd = CodexAdapter().build_command(
            role, session_id=UUID_A, is_resume=True, prompt="continue",
        )
        # Form: codex --dangerously-... [--model X] resume <uuid>
        assert cmd[0] == "codex"
        assert "resume" in cmd
        resume_idx = cmd.index("resume")
        assert cmd[resume_idx + 1] == UUID_A
        # prompt is NOT in argv (delivered via send-keys)
        assert "continue" not in cmd

    def test_resume_still_includes_bypass_flag(self):
        role = RoleConfig(name="dev")
        cmd = CodexAdapter().build_command(
            role, session_id=UUID_A, is_resume=True, prompt="x",
        )
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_resume_without_session_id_falls_back_to_fresh(self):
        """is_resume=True but session_id=None must not raise; caller
        (SpawnCoordinator) is expected to emit a warning event and let
        the adapter produce a fresh-start command. NEVER use --last."""
        role = RoleConfig(name="dev")
        cmd = CodexAdapter().build_command(
            role, session_id=None, is_resume=True, prompt="x",
        )
        assert "resume" not in cmd
        assert "--last" not in cmd
        assert cmd[0] == "codex"


class TestMockAdapterIgnoresNewArgs:
    def test_mock_adapter_accepts_extra_kwargs(self):
        role = RoleConfig(name="dev")
        cmd = MockAdapter().build_command(
            role, session_id=UUID_A, is_resume=True, prompt="hi",
        )
        # Mock uses `cat`, doesn't care about any of this
        assert cmd == ["cat"]


class TestBackwardCompat:
    def test_claude_adapter_called_positionally_with_role_only_still_works(self):
        """Legacy: build_command(role) with no extras should still work
        (all new params default to None/False)."""
        role = RoleConfig(name="dev", permission_mode="bypass")
        cmd = ClaudeCodeAdapter().build_command(role)
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd

    def test_get_adapter_still_returns_correct_type(self):
        assert isinstance(get_adapter("claude-code"), ClaudeCodeAdapter)
        assert isinstance(get_adapter("codex"), CodexAdapter)
        assert isinstance(get_adapter("mock"), MockAdapter)
