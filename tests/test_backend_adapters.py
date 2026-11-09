"""Tests for ClaudeCode and Codex adapters."""

from __future__ import annotations

from zf.runtime.backend import ClaudeCodeAdapter, CodexAdapter, get_adapter
from zf.runtime.provider_permissions import (
    claude_permission_mode_for_profile,
    codex_security_config_for_profile,
)
from zf.core.config.schema import RoleConfig


class TestClaudeCodeAdapterFull:
    def test_all_roles_launch_with_full_provider_permissions(self):
        for permission_mode in ("bypass", "default", "restricted", "allowlist", ""):
            role = RoleConfig(name="dev", permission_mode=permission_mode)
            cmd = ClaudeCodeAdapter().build_command(role)
            assert "--dangerously-skip-permissions" in cmd

    def test_model_flag_when_set(self):
        role = RoleConfig(name="dev", model="opus")
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--model" in cmd
        assert "opus" in cmd

    def test_no_model_flag_when_empty(self):
        # P-Y1: empty model → use backend CLI default, no --model flag
        role = RoleConfig(name="dev", model="")
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--model" not in cmd

    def test_no_model_flag_when_legacy_placeholder(self):
        # Backward compat: old yaml files with model: placeholder still
        # behave as "use default" instead of passing the literal string.
        role = RoleConfig(name="dev", model="placeholder")
        cmd = ClaudeCodeAdapter().build_command(role)
        assert "--model" not in cmd


class TestCodexAdapter:
    def test_build_command(self):
        role = RoleConfig(name="dev")
        cmd = CodexAdapter().build_command(role)
        assert cmd[0] == "codex"
        # Interactive TUI: no exec subcommand
        assert "exec" not in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_clear_is_none(self):
        assert CodexAdapter().clear_command is None

    def test_get_adapter_codex(self):
        adapter = get_adapter("codex")
        assert isinstance(adapter, CodexAdapter)

    def test_model_flag(self):
        role = RoleConfig(name="dev", model="gpt-4")
        cmd = CodexAdapter().build_command(role)
        assert "--model" in cmd
        assert "gpt-4" in cmd

    def test_model_reasoning_effort_overrides_global_codex_config(self):
        role = RoleConfig(
            name="dev",
            model="gpt-5.5",
            model_reasoning_effort="xhigh",
        )
        cmd = CodexAdapter().build_command(role)
        assert "--config" in cmd
        assert 'model_reasoning_effort="xhigh"' in cmd

    def test_empty_model_reasoning_effort_preserves_cli_inheritance(self):
        role = RoleConfig(name="dev", model="gpt-5.5")
        cmd = CodexAdapter().build_command(role)
        assert not any("model_reasoning_effort" in item for item in cmd)

    def test_ready_pattern_is_unicode_chevron(self):
        # Codex TUI uses U+203A (›), not ASCII >
        assert CodexAdapter().ready_pattern == "›"

    def test_enables_codex_hooks_feature_flag(self):
        # 1202-T1 / 2026-05: current Codex uses the stable `hooks`
        # feature name; the legacy `codex_hooks` key now warns.
        role = RoleConfig(name="dev")
        cmd = CodexAdapter().build_command(role)
        assert "--enable" in cmd
        assert "hooks" in cmd


class TestCodexPermissionMode:
    """All worker roles share the trusted full-access Codex baseline."""

    def test_all_permission_modes_use_dangerous_provider_flag(self):
        for permission_mode in ("bypass", "default", "restricted", "allowlist", ""):
            role = RoleConfig(name="test", permission_mode=permission_mode)
            cmd = CodexAdapter().build_command(role)
            assert "--dangerously-bypass-approvals-and-sandbox" in cmd
            assert "--full-auto" not in cmd
            assert "-a" not in cmd
            assert "-s" not in cmd


class TestHeadlessProviderPermissionDefaults:
    def test_all_profiles_keep_the_full_provider_baseline(self):
        for profile in (
            "read_only",
            "operator",
            "artifact_writer",
            "project_writer",
            "workspace_writer",
            "isolated_writer",
            "dangerous_full",
        ):
            codex = codex_security_config_for_profile(profile, env={})
            assert codex == {
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            }
            assert claude_permission_mode_for_profile(profile, env={}) == "bypassPermissions"


class TestCodexNativeCompaction:
    def test_compacts_before_harness_recycle_without_overriding_model_window(self):
        role = RoleConfig(name="planner", backend="codex")
        for resume in (False, True):
            command = CodexAdapter().build_command(
                role, session_id="session-1", is_resume=resume,
            )
            assert "model_auto_compact_token_limit=140000" in command
            assert not any(item.startswith("model_context_window=") for item in command)
            if resume:
                assert command[-2:] == ["resume", "session-1"]

    def test_uses_configured_role_compaction_budget(self):
        role = RoleConfig(name="planner", backend="codex", context_window_tokens=400_000,
                          context_compact_threshold=0.65)
        assert "model_auto_compact_token_limit=260000" in CodexAdapter().build_command(role)
