"""A tmux session lifecycle must not target another session by prefix."""
from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest

from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def test_transport_init_preserves_similarly_named_session(monkeypatch):
    sessions = {"zf-ls-control"}
    tmux = TmuxSession(session_name="zf-ls")

    def run(args, *, check=True, capture=False):
        verb = args[1]
        if verb in {"has-session", "kill-session"}:
            target = args[3]
            matches = {target[1:]} & sessions if target.startswith("=") else {
                name for name in sessions if name.startswith(target)
            }
            if verb == "kill-session":
                sessions.difference_update(matches)
            return subprocess.CompletedProcess(args, 0 if matches else 1, stdout="", stderr="")
        if verb == "new-session":
            sessions.add(args[args.index("-s") + 1])
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(tmux, "_run", run)
    TmuxTransport(tmux).init()
    assert sessions == {"zf-ls", "zf-ls-control"}
    tmux.kill_session()
    assert sessions == {"zf-ls-control"}
    assert not tmux.has_session()


@pytest.mark.host
def test_real_tmux_similar_session_survives_lifecycle():
    base = "zf-exact-" + uuid4().hex[:10]
    other = base + "-operator"
    subprocess.run(["tmux", "new-session", "-d", "-s", other], check=True)
    try:
        tmux = TmuxSession(session_name=base)
        assert not tmux.has_session()
        TmuxTransport(tmux).init()
        assert tmux.has_session()
        tmux.kill_session()
        assert not tmux.has_session()
        assert subprocess.run(["tmux", "has-session", "-t", "=" + other],
                              capture_output=True).returncode == 0
    finally:
        subprocess.run(["tmux", "kill-session", "-t", "=" + base], capture_output=True)
        subprocess.run(["tmux", "kill-session", "-t", "=" + other], capture_output=True)
