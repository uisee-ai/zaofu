from __future__ import annotations

from zf.runtime.transport import TmuxTransport


def test_tmux_agent_env_prefix_carries_runtime_state(monkeypatch):
    monkeypatch.setenv("ZF_PROJECT_ROOT", "/tmp/project")
    monkeypatch.setenv("ZF_STATE_DIR", "/tmp/project/.zf-custom")
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")
    monkeypatch.setenv("PATH", "/usr/bin")

    prefix = TmuxTransport._agent_env_prefix()

    assert "ZF_PROJECT_ROOT=/tmp/project" in prefix
    assert "ZF_STATE_DIR=/tmp/project/.zf-custom" in prefix
    assert "ZF_CLI_CMD=uv --project /repo run zf" in prefix
