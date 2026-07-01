"""O-1: tmux commands must not inherit nested-tmux env (doc 78 ops).

When ZaoFu's watcher/orchestrator runs inside a tmux pane (e.g. launched by a
background runner that inherits TMUX/TMUX_PANE), tmux treats every `tmux`
subprocess as a nested client bound to the caller's pane/server. `new-session`
then warns/refuses and `-t <session>` can resolve against the wrong server →
"can't find session" → the harness freezes (observed: a 24h stall). Stripping
TMUX/TMUX_PANE makes each command operate on the default server and target the
harness's own named session deterministically.
"""

from __future__ import annotations

from zf.runtime.tmux import TmuxSession, tmux_env


def test_tmux_env_strips_nested_vars():
    base = {"PATH": "/usr/bin", "TMUX": "/tmp/tmux-1000/default,123,0", "TMUX_PANE": "%4"}
    env = tmux_env(base)
    assert "TMUX" not in env
    assert "TMUX_PANE" not in env
    assert env["PATH"] == "/usr/bin"  # other vars preserved


def test_tmux_env_no_nested_vars_is_passthrough():
    base = {"PATH": "/usr/bin", "HOME": "/home/x"}
    assert tmux_env(base) == base


def test_run_passes_nested_free_env(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["env"] = kwargs.get("env")
        import subprocess
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,99,0")
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setattr("zf.runtime.tmux.subprocess.run", fake_run)

    TmuxSession(session_name="zf").has_session()

    env = captured["env"]
    assert env is not None
    assert "TMUX" not in env
    assert "TMUX_PANE" not in env
