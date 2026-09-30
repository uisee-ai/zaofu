"""Owned tree selection and PID reuse guards, with no real process signals."""

import os
import signal
import subprocess

import pytest

from zf.runtime import tmux_process_cleanup as cleanup
from zf.runtime.tmux import TmuxSession


def test_selection_is_leaf_first_and_excludes_roots_and_foreign_tree():
    rows = {100: (9, "a"), 101: (100, "b"), 102: (101, "c"),
            200: (9, "d"), 201: (200, "e")}
    assert cleanup._descendants(rows, {100}) == [102, 101]
    with pytest.raises(RuntimeError, match="calling process"):
        cleanup._descendants({os.getpid(): (100, "x")}, {100})


@pytest.mark.parametrize("changed", [None, "birth", "parent", "exited"])
def test_pinned_identity_rechecked_before_signal_and_always_closed(monkeypatch, changed):
    rows = {100: (9, "a"), 101: (100, "b"), 102: (101, "c")}
    sent, closed, waits = [], [], []
    monkeypatch.setattr(cleanup.sys, "platform", "linux")
    monkeypatch.setattr(cleanup, "_processes", lambda: rows)
    def identity(pid):
        if pid == 102:
            return {None: rows[pid], "birth": (101, "reused"), "parent": (200, "c"), "exited": None}[changed]
        return rows[pid]
    monkeypatch.setattr(cleanup, "_identity", identity)
    class API:
        def open(self, pid):
            return pid + 1000
        def send(self, handle, signum):
            sent.append((handle, signum))
    monkeypatch.setattr(cleanup, "_Pidfd", API)
    monkeypatch.setattr(cleanup.os, "close", closed.append)
    def wait(handles, seconds):
        waits.append(list(handles))
        return handles if len(waits) == 1 else []
    monkeypatch.setattr(cleanup, "_wait", wait)
    cleanup.terminate_pane_descendants({100})
    expected = [1102, 1101] if changed is None else [1101]
    assert sent == [(fd, sig) for sig in (signal.SIGTERM, signal.SIGKILL) for fd in expected]
    assert sorted(closed) == [1101, 1102]


def test_cleanup_failure_is_not_silently_successful(monkeypatch):
    monkeypatch.setattr(cleanup, "_processes", lambda: {100: (9, "a"), 101: (100, "b")})
    monkeypatch.setattr(cleanup, "_identity", lambda pid: (100, "b"))
    closed = []
    monkeypatch.setattr(cleanup.os, "close", closed.append)
    class API:
        def open(self, pid):
            return 777
        def send(self, *args):
            pass
    monkeypatch.setattr(cleanup, "_Pidfd", API)
    monkeypatch.setattr(cleanup, "_wait", lambda handles, seconds: handles)
    with pytest.raises(RuntimeError, match="did not exit"):
        cleanup.terminate_pane_descendants({100})
    assert closed == [777]


def test_actual_tmux_entry_cleans_only_live_exact_session_roots(monkeypatch):
    calls, roots = [], []
    tmux = TmuxSession("owned-session")
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="100 0\n200 1\n", stderr="")
    monkeypatch.setattr(tmux, "_run", run)
    monkeypatch.setattr(cleanup, "terminate_pane_descendants", roots.append)
    tmux.kill_session()
    assert roots == [{100}]
    assert calls[0] == ["tmux", "list-panes", "-s", "-t", "=owned-session", "-F", "#{pane_pid} #{pane_dead}"]
    assert calls[1][:2] == ["tmux", "kill-session"]


def test_missing_session_does_not_discover_or_signal_processes(monkeypatch):
    tmux = TmuxSession("gone-session")
    monkeypatch.setattr(tmux, "_run", lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout=""))
    monkeypatch.setattr(cleanup, "terminate_pane_descendants", lambda roots: pytest.fail("no authority"))
    tmux.kill_session()
