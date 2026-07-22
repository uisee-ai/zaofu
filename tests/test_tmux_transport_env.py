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


def test_agent_env_prefix_unsets_foreign_virtualenv(monkeypatch, tmp_path):
    foreign_venv = "/home/user/source-checkout/.venv"
    monkeypatch.setenv("VIRTUAL_ENV", foreign_venv)
    monkeypatch.setenv("PATH", "/usr/bin")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    prefix = TmuxTransport._agent_env_prefix(worktree)

    assert "-u VIRTUAL_ENV" in prefix
    assert f"VIRTUAL_ENV={foreign_venv}" not in prefix


def test_agent_env_prefix_keeps_matching_virtualenv(monkeypatch, tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    matching_venv = str((worktree / ".venv").resolve())
    monkeypatch.setenv("VIRTUAL_ENV", matching_venv)
    monkeypatch.setenv("PATH", "/usr/bin")

    prefix = TmuxTransport._agent_env_prefix(worktree)

    assert "-u VIRTUAL_ENV" not in prefix
    assert f"VIRTUAL_ENV={matching_venv}" in prefix
