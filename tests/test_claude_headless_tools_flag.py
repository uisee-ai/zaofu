"""claude-headless `--tools` flag: an unset env must NOT disable all tools.

Regression for the 2026-06-22 finding: ZF_KANBAN_AGENT_CLAUDE_HEADLESS_TOOLS
defaulted to "" and the code passed `--tools ""` (an empty allowlist), so claude
could not Read/Bash and described tools as text — rich tool cards broke for the
claude backend. Empty / "default" → no flag (all tools); an explicit list passes.
"""

from __future__ import annotations

from zf.web.headless_agent import ClaudeHeadlessBackend


def _args(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_TOOLS", raising=False)
    else:
        monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_TOOLS", value)
    return ClaudeHeadlessBackend().build_args(thread_id="t1")


def test_unset_env_does_not_pass_tools_flag(monkeypatch):
    args = _args(monkeypatch, None)
    assert "--tools" not in args  # all tools, not an empty allowlist


def test_empty_env_does_not_pass_tools_flag(monkeypatch):
    assert "--tools" not in _args(monkeypatch, "")


def test_default_keyword_does_not_pass_tools_flag(monkeypatch):
    assert "--tools" not in _args(monkeypatch, "default")


def test_explicit_list_passes_tools_flag(monkeypatch):
    args = _args(monkeypatch, "Read,Bash")
    assert "--tools" in args
    assert args[args.index("--tools") + 1] == "Read,Bash"
