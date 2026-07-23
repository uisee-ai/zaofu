"""Tests for ClaudeCode and Codex adapters."""

from __future__ import annotations

from zf.runtime.backend import ClaudeCodeAdapter, CodexAdapter, get_adapter
from zf.core.config.schema import RoleConfig


class TestClaudeCodeAdapterFull:
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
    """1231-T2: Codex three-tier permission mapping — bypass / default /
    restricted. Aligns with Codex's real `-a / --ask-for-approval` ×
    `-s / --sandbox` model (no `--allowed-tool` flag exists).
    """

    def test_bypass_keeps_dangerous_flag(self):
        role = RoleConfig(name="dev", permission_mode="bypass")
        cmd = CodexAdapter().build_command(role)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--full-auto" not in cmd
        assert "-a" not in cmd

    def test_default_maps_to_workspace_write_without_prompt(self):
        """Default mode uses current Codex flags, not removed --full-auto."""
        role = RoleConfig(name="dev", permission_mode="default")
        cmd = CodexAdapter().build_command(role)
        assert "-a" in cmd and "never" in cmd
        assert "-s" in cmd and "workspace-write" in cmd
        assert "--full-auto" not in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_empty_permission_mode_defaults_to_workspace_write_without_prompt(self):
        """Empty string (zaofu loader default) behaves like 'default'."""
        role = RoleConfig(name="dev", permission_mode="")
        cmd = CodexAdapter().build_command(role)
        assert "-a" in cmd and "never" in cmd
        assert "-s" in cmd and "workspace-write" in cmd
        assert "--full-auto" not in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_restricted_without_allowed_paths_uses_read_only(self):
        """Pure restricted (no writable paths declared) → read-only sandbox."""
        role = RoleConfig(name="test", permission_mode="restricted")
        cmd = CodexAdapter().build_command(role)
        assert "-a" in cmd and "untrusted" in cmd
        assert "-s" in cmd and "read-only" in cmd
        assert "--add-dir" not in cmd, \
            "--add-dir must NOT appear alongside -s read-only (B-1203-01)"
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_allowlist_is_alias_for_restricted(self):
        """Legacy zaofu yaml uses `allowlist`; maps to the same flags as
        `restricted` for codex (no tool allowlist exists upstream)."""
        role = RoleConfig(name="test", permission_mode="allowlist")
        cmd = CodexAdapter().build_command(role)
        assert "untrusted" in cmd
        assert "read-only" in cmd

    def test_restricted_honors_explicit_danger_full_access_override(self, monkeypatch):
        """Trusted local smoke can bypass unsupported host sandboxing explicitly."""
        monkeypatch.setenv("ZF_CODEX_WORKER_SANDBOX", "danger-full-access")
        role = RoleConfig(name="test", permission_mode="restricted")
        cmd = CodexAdapter().build_command(role)
        assert "-a" in cmd and cmd[cmd.index("-a") + 1] == "never"
        assert "-s" in cmd and cmd[cmd.index("-s") + 1] == "danger-full-access"
        assert "read-only" not in cmd

    def test_restricted_ignores_unsupported_worker_sandbox_override(self, monkeypatch):
        monkeypatch.setenv("ZF_CODEX_WORKER_SANDBOX", "workspace-write")
        role = RoleConfig(name="test", permission_mode="restricted")
        cmd = CodexAdapter().build_command(role)
        assert cmd[cmd.index("-s") + 1] == "read-only"

    def test_worker_sandbox_does_not_inherit_kanban_headless_override(self, monkeypatch):
        monkeypatch.delenv("ZF_CODEX_WORKER_SANDBOX", raising=False)
        monkeypatch.setenv(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX",
            "danger-full-access",
        )
        role = RoleConfig(name="test", permission_mode="restricted")
        cmd = CodexAdapter().build_command(role)
        assert cmd[cmd.index("-s") + 1] == "read-only"

    def test_restricted_with_allowed_paths_uses_workspace_write(self):
        """B-1203-01 (2026-04-21): codex aborts when `-s read-only` is
        combined with `--add-dir`. Upgrade sandbox to workspace-write
        so declared paths are actually writable."""
        from zf.core.config.schema import ConstraintsConfig
        role = RoleConfig(
            name="test",
            permission_mode="restricted",
            constraints=ConstraintsConfig(allowed_paths=["tests/", "fixtures/"]),
        )
        cmd = CodexAdapter().build_command(role)
        # Upgraded sandbox
        assert "-s" in cmd
        s_idx = cmd.index("-s")
        assert cmd[s_idx + 1] == "workspace-write", \
            f"restricted+allowed_paths must use workspace-write, got {cmd[s_idx + 1]}"
        # read-only must NOT appear
        assert "read-only" not in cmd
        # Both allowed paths become --add-dir
        occurrences = [i for i, a in enumerate(cmd) if a == "--add-dir"]
        assert len(occurrences) == 2, f"expected 2 --add-dir, got cmd={cmd}"
        values = [cmd[i + 1] for i in occurrences]
        assert "tests/" in values and "fixtures/" in values

    def test_unknown_permission_mode_falls_through_to_workspace_write(self):
        """Safer to fall back than error — unknown mode shouldn't brick
        the spawn, just log via warning elsewhere."""
        role = RoleConfig(name="dev", permission_mode="nonsense-mode")
        cmd = CodexAdapter().build_command(role)
        assert "-a" in cmd and "never" in cmd
        assert "-s" in cmd and "workspace-write" in cmd
        assert "--full-auto" not in cmd
