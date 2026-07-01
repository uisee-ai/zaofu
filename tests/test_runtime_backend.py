"""Tests for backend adapter abstraction."""

from __future__ import annotations

import pytest

from zf.runtime.backend import (
    BackendAdapter,
    ClaudeCodeAdapter,
    MockAdapter,
    get_adapter,
)
from zf.core.config.schema import RoleConfig, ExecutionConfig


class TestMockAdapter:
    def test_is_backend_adapter(self):
        assert isinstance(MockAdapter(), BackendAdapter)

    def test_build_command(self):
        role = RoleConfig(name="dev")
        cmd = MockAdapter().build_command(role)
        assert isinstance(cmd, list)
        assert len(cmd) >= 1

    def test_ready_pattern(self):
        assert isinstance(MockAdapter().ready_pattern, str)
        assert len(MockAdapter().ready_pattern) > 0

    def test_clear_command(self):
        adapter = MockAdapter()
        # mock adapter may or may not have clear
        assert adapter.clear_command is None or isinstance(adapter.clear_command, str)


class TestClaudeCodeAdapter:
    def test_is_backend_adapter(self):
        assert isinstance(ClaudeCodeAdapter(), BackendAdapter)

    def test_build_command_basic(self):
        role = RoleConfig(name="dev")
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "claude" in cmd[0]

    def test_build_command_default_uses_dangerously_skip(self):
        role = RoleConfig(name="dev")  # default permission_mode = bypass
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--dangerously-skip-permissions" in cmd

    def test_build_command_allowlist_uses_allowed_tools(self):
        role = RoleConfig(
            name="dev",
            permission_mode="allowlist",
            allowed_tools=["Read", "Bash(pytest *)"],
        )
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--dangerously-skip-permissions" not in cmd
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert "Read" in cmd[idx + 1]
        assert "Bash(pytest *)" in cmd[idx + 1]

    def test_build_command_allowlist_without_tools_falls_back_safely(self):
        role = RoleConfig(name="dev", permission_mode="allowlist", allowed_tools=[])
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--dangerously-skip-permissions" not in cmd
        assert "--allowedTools" not in cmd

    def test_ready_pattern(self):
        pattern = ClaudeCodeAdapter().ready_pattern
        assert isinstance(pattern, str)

    def test_clear_command(self):
        assert ClaudeCodeAdapter().clear_command == "/clear"


class TestGetAdapter:
    def test_get_claude_code(self):
        adapter = get_adapter("claude-code")
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_get_mock(self):
        adapter = get_adapter("mock")
        assert isinstance(adapter, MockAdapter)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            get_adapter("nonexistent")

    def test_python_backend_returns_mock(self):
        """The 'python' backend in zf.yaml maps to mock for now."""
        adapter = get_adapter("python")
        assert isinstance(adapter, MockAdapter)
