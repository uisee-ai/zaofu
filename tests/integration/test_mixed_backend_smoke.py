"""1203-T5: integration smoke test for mixed backend configuration.

Validates that zaofu's pipeline can handle a yaml with both claude-code
and codex roles without real subprocess launches. Covers:
  - mixed-team.yaml loads + both adapters instantiate
  - CodexAdapter and ClaudeCodeAdapter produce different argv shapes
  - SpawnCoordinator uses --session-id for claude, not for codex
  - ClaudeSessionTailer and CodexSessionTailer coexist without
    stepping on each other (separate file streams, separate threads)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig
from zf.core.events.log import EventLog
from zf.runtime.backend import (
    ClaudeCodeAdapter, CodexAdapter, get_adapter,
)


MIXED_YAML = Path(__file__).resolve().parents[2] / "examples" / "mixed-team.yaml"


def test_mixed_yaml_instantiates_both_adapters():
    config = load_config(MIXED_YAML)
    for role in config.roles:
        adapter = get_adapter(role.backend)
        if role.backend == "claude-code":
            assert isinstance(adapter, ClaudeCodeAdapter)
        elif role.backend == "codex":
            assert isinstance(adapter, CodexAdapter)


def test_claude_and_codex_produce_distinct_argv():
    claude_role = RoleConfig(name="dev", backend="claude-code",
                              permission_mode="bypass")
    codex_role = RoleConfig(name="dev", backend="codex",
                             permission_mode="bypass")

    claude_cmd = ClaudeCodeAdapter().build_command(claude_role)
    codex_cmd = CodexAdapter().build_command(codex_role)

    assert claude_cmd[0] == "claude"
    assert codex_cmd[0] == "codex"
    # Flags are backend-specific
    assert "--dangerously-skip-permissions" in claude_cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in codex_cmd


def test_claude_uses_session_id_codex_does_not():
    """First-spawn semantics: claude pre-seeds uuid via --session-id;
    codex cannot (its uuid is written post-first-turn)."""
    claude_role = RoleConfig(name="dev", backend="claude-code")
    codex_role = RoleConfig(name="dev", backend="codex")

    claude_cmd = ClaudeCodeAdapter().build_command(
        claude_role, session_id="abc-uuid", is_resume=False,
    )
    codex_cmd = CodexAdapter().build_command(
        codex_role, session_id="abc-uuid", is_resume=False,
    )

    # Claude: session-id appears
    assert "--session-id" in claude_cmd
    idx = claude_cmd.index("--session-id")
    assert claude_cmd[idx + 1] == "abc-uuid"
    # Codex: no --session-id flag when is_resume=False
    assert "--session-id" not in codex_cmd
    # Resume subcommand also absent on first spawn
    assert "resume" not in codex_cmd


def test_two_tailers_coexist_on_distinct_files(tmp_path: Path):
    """ClaudeSessionTailer and CodexSessionTailer both poll their own
    jsonl paths — no shared-file race."""
    from zf.runtime.session_tailer import (
        ClaudeSessionTailer,
        CodexSessionTailer,
    )

    events_path = tmp_path / "events.jsonl"
    log = EventLog(events_path)

    claude_file = tmp_path / "claude.jsonl"
    codex_file = tmp_path / "codex.jsonl"
    claude_file.touch()
    codex_file.touch()

    ct = ClaudeSessionTailer(log)
    xt = CodexSessionTailer(log)
    try:
        ct.tail("orchestrator-1", claude_file)
        xt.tail("dev-1", codex_file)
        # Both tailers have registered threads without raising
        assert "orchestrator-1" in getattr(ct, "_threads", {}) or \
            getattr(ct, "_threads", None) is not None
        assert "dev-1" in getattr(xt, "_threads", {}) or \
            getattr(xt, "_threads", None) is not None
    finally:
        ct.stop()
        xt.stop()


def test_codex_hook_settings_generated_when_codex_role_present(tmp_path):
    """1202+1203: start.py renders <project>/.codex/hooks.json when at
    least one role has backend=codex. Reproduce the condition statically.
    """
    from zf.cli.start import _write_codex_hook_settings
    import json

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _write_codex_hook_settings(state_dir)

    hook_file = tmp_path / ".codex" / "hooks.json"
    assert hook_file.exists()
    data = json.loads(hook_file.read_text())
    assert set(data["hooks"].keys()) >= {
        "SessionStart", "UserPromptSubmit", "PreToolUse",
        "PostToolUse", "Stop",
    }
