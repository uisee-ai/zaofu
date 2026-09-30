"""Current Claude TUI blockers must not become ready/dispatch evidence."""

import json
from pathlib import Path

import pytest

from tests.test_spawn_coordinator import _RecordingTransport
from tests.test_tmux_transport_lifecycle import _FakeTmux
from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.backend import ClaudeCodeAdapter
from zf.runtime.provider_interactive_prompt import provider_interactive_prompt_marker
from zf.runtime.spawn_coordinator import SpawnCoordinator, purge_stale_claude_session_lock
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


class ClaudeInteractiveReadinessBug(AssertionError):
    """A provider still waiting for user input was accepted as dispatch-ready."""


TRUST_SCREEN = """Accessing workspace:
/tmp/zf-owned-fixture/project
Quick safety check: Is this a project you created or one you trust?
Claude Code'll be able to read, edit, and execute files here.
❯ No, exit
  Yes, I trust this folder
Enter to confirm · Esc to cancel
"""
ONBOARDING_SCREEN = """Welcome to Claude Code v2.1.275
Security notes:
1. Claude can make mistakes.
2. Due to prompt injection risks, only use it with code you trust
Press Enter to continue…
"""


def blocked_claude_screen(screen: str) -> bool:
    return bool(provider_interactive_prompt_marker(screen))


@pytest.mark.parametrize(
    "screen, expected",
    [(TRUST_SCREEN, "trust_prompt"), (ONBOARDING_SCREEN, "onboarding_prompt")],
    ids=["trust", "onboarding"],
)
def test_current_claude_startup_screen_is_classified_as_blocked(screen, expected):
    assert provider_interactive_prompt_marker(screen) == expected


@pytest.mark.parametrize("screen", [TRUST_SCREEN, "❯ stale prompt\n" + ONBOARDING_SCREEN], ids=["trust", "stale-prompt-onboarding"])
def test_tmux_readiness_rejects_current_claude_startup_screen(screen):
    class ScreenTmux(_FakeTmux):
        # Exercise production regex matching as well as the real blocker classifier.
        wait_for_prompt = TmuxSession.wait_for_prompt

        def capture_pane(self, role_name, *, lines=120):
            return screen

    tmux = ScreenTmux(command="claude")
    transport = TmuxTransport(tmux)
    transport._launch_records["impl-1"] = {"backend": "claude-code", "launch_attempts": 1}
    if transport.wait_ready("impl-1", ClaudeCodeAdapter().ready_pattern, timeout=0.1):
        raise ClaudeInteractiveReadinessBug("TmuxTransport.wait_ready=True while Claude awaits human confirmation")
    assert not tmux.sent


def test_tmux_readiness_rejects_permission_confirmation_screen():
    screen = "Contains shell syntax ($) that cannot be statically analyzed\nDo you want to proceed?"
    assert blocked_claude_screen(screen)


def test_tmux_readiness_rejects_generic_command_approval_screen():
    screen = "This command requires approval\n\nDo you want to proceed?\n❯ 1. Yes"
    assert blocked_claude_screen(screen)


@pytest.mark.parametrize("screen, expected", [("❯ Ready for a task", ""), ("Do you trust the files in this folder?", "trust_prompt")])
def test_existing_ready_and_legacy_trust_controls(screen, expected):
    assert provider_interactive_prompt_marker(screen) == expected


class ClaudeConfigTrustPathBug(AssertionError):
    """Spawn prepared trust in a file the configured provider does not read."""


@pytest.mark.parametrize("configured", [False, True, ""], ids=["default-home", "configured-home", "empty-override"])
def test_spawn_trust_uses_effective_claude_config_directory(
    tmp_path, monkeypatch, record_property, configured,
):
    provider_home = tmp_path / "provider-home"
    provider_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: provider_home))
    config_root = provider_home / ".claude" if configured else provider_home
    config_root.mkdir(exist_ok=True)
    if configured:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_root))
    elif configured == "":
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    else:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    effective_config = config_root / ".claude.json"
    effective_config.write_text('{"projects": {}}', encoding="utf-8")
    state = tmp_path / "runtime-state"
    state.mkdir()
    worktree = tmp_path / "project"
    worktree.mkdir()
    physical = _RecordingTransport()
    registry = RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(worktree))
    coordinator = SpawnCoordinator(
        state_dir=state, registry=registry, transport=physical, project_root=str(worktree),
    )
    role = RoleConfig(name="impl", backend="claude-code", permission_mode="allowlist", allowed_tools=["Read"])
    original_spawn = physical.spawn

    def observe_spawn(role, argv, *, cwd=None):
        entry = json.loads(effective_config.read_text())["projects"].get(str(cwd.resolve()), {})
        record_property("claude_trust_config", json.dumps({
            "configured": configured, "effective_config": str(effective_config),
            "trusted_before_spawn": entry.get("hasTrustDialogAccepted") is True,
        }))
        assert "--dangerously-skip-permissions" not in argv
        if entry.get("hasTrustDialogAccepted") is not True:
            raise ClaudeConfigTrustPathBug("effective Claude config has no workspace trust at spawn")
        original_spawn(role, argv, cwd=cwd)

    monkeypatch.setattr(physical, "spawn", observe_spawn)
    coordinator.spawn(role, cwd=worktree)
    assert len(physical.spawn_calls) == 1


def test_stale_session_config_cleanup_respects_config_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    config_root = tmp_path / "custom-config"
    config_root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_root))
    monkeypatch.setattr("zf.runtime.spawn_coordinator._uuid_used_by_live_process", lambda _: False)
    stale_id = "f3cf894b-e8f0-43e0-b488-d06a0b5b8711"
    original = json.dumps({"projects": {"/isolated-project": {
        "lastSessionId": stale_id, "lastHintSessionId": stale_id,
        "hasTrustDialogAccepted": True,
    }}})
    legacy = tmp_path / ".claude.json"
    effective = config_root / ".claude.json"
    for path in (legacy, effective):
        path.write_text(original, encoding="utf-8")
    result = purge_stale_claude_session_lock(stale_id)
    assert len(result["claude_json_fields_cleared"]) == 2
    assert legacy.read_text() == original
    assert json.loads(effective.read_text())["projects"]["/isolated-project"] == {
        "lastSessionId": "", "lastHintSessionId": "", "hasTrustDialogAccepted": True,
    }
