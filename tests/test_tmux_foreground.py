from pathlib import Path

import pytest

from zf.runtime import tmux_foreground
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def proc(root, pid, *, name="codex", parent=100, group=None, session=100,
         tty=123, foreground=200, started=99, state="S"):
    fields = [state, parent, group or pid, session, tty, foreground, *([0] * 13), started]
    folder = root / str(pid)
    folder.mkdir(exist_ok=True)
    (folder / "stat").write_text(f"{pid} ({name}) " + " ".join(map(str, fields)))


@pytest.fixture
def tree(tmp_path):
    proc(tmp_path, 100, name="bash", parent=50, started=10)
    proc(tmp_path, 200)
    return tmp_path


def test_actual_tmux_transport_recognizes_proven_foreground_with_shell_format(tree, monkeypatch):
    original = tmux_foreground.foreground_process
    monkeypatch.setattr(tmux_foreground, "foreground_process",
                        lambda pid: original(pid, proc_root=tree))
    tmux = TmuxSession("isolated")
    monkeypatch.setattr(tmux, "pane_pid", lambda role: "100")
    monkeypatch.setattr(tmux, "pane_alive", lambda role: True)
    monkeypatch.setattr(tmux, "pane_current_command", lambda role: "bash")
    monkeypatch.setattr(tmux, "capture_pane", lambda *args, **kwargs: "")
    monkeypatch.setattr(tmux, "wait_for_prompt", lambda *args, **kwargs: True)
    transport = TmuxTransport(tmux)
    assert transport.is_alive("dev")
    assert transport.wait_ready("dev", "prompt", timeout=1)
    # The same provider as a background job must never make the shell executable.
    proc(tree, 100, name="bash", parent=50, foreground=100, started=10)
    assert not transport.is_alive("dev")


@pytest.mark.parametrize("changes", [
    {"foreground": 300}, {"group": 300}, {"tty": 456}, {"tty": 0},
    {"session": 300}, {"parent": 1}, {"parent": 200}, {"state": "Z"},
])
def test_unbound_or_dead_foreground_is_not_evidence(tree, changes):
    proc(tree, 200, **changes)
    assert tmux_foreground.foreground_process("100", proc_root=tree) is None


def test_foreground_must_still_belong_to_same_pane_after_read(tree, monkeypatch):
    original = tmux_foreground._read
    calls = 0

    def read(pid, root):
        nonlocal calls
        calls += 1
        if calls == 3:
            proc(root, 100, name="bash", parent=50, started=11)
        return original(pid, root)

    monkeypatch.setattr(tmux_foreground, "_read", read)
    assert tmux_foreground.foreground_process("100", proc_root=tree) is None


def test_foreground_descendant_wrapper_and_parentheses_in_process_name(tree):
    proc(tree, 150, name="wrapper (test)")
    proc(tree, 200, parent=150)
    assert tmux_foreground.foreground_process("100", proc_root=tree).name == "codex"


@pytest.mark.parametrize("pid", ["", "-1", "1", "garbage", "999"])
def test_unavailable_process_or_non_linux_host_is_closed(tree: Path, pid):
    assert tmux_foreground.foreground_process(pid, proc_root=tree) is None


def test_captured_foreground_identity_survives_shell_exit_but_not_pid_reuse(tree):
    worker = tmux_foreground.foreground_process("100", proc_root=tree)
    (tree / "100" / "stat").unlink()
    proc(tree, 200, parent=1, foreground=-1, tty=0)
    assert tmux_foreground.same_process_group(worker, proc_root=tree)
    proc(tree, 200, parent=1, started=100)
    assert not tmux_foreground.same_process_group(worker, proc_root=tree)


def test_actual_termination_signals_owned_foreground_even_when_pane_disappears(tree, monkeypatch):
    import signal
    from types import SimpleNamespace

    worker = tmux_foreground.foreground_process("100", proc_root=tree)
    monkeypatch.setattr(tmux_foreground, "foreground_process", lambda pid: worker)
    monkeypatch.setattr(tmux_foreground, "same_process_group", lambda process: True)
    tmux = TmuxSession("isolated")
    monkeypatch.setattr(tmux.layout, "role_absence_confirmed", lambda *args: False)
    monkeypatch.setattr(tmux.layout, "resolve", lambda *args: SimpleNamespace(address=lambda: "isolated:dev"))
    monkeypatch.setattr(tmux.layout, "target_owned_by", lambda *args: True)
    monkeypatch.setattr(tmux, "_pane_pid_for_target", lambda *args: "100")
    monkeypatch.setattr(tmux, "_wait_until_target_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(tmux, "_hard_kill_target", lambda *args, **kwargs: None)
    sent = []
    monkeypatch.setattr(tmux, "_send_signal",
                        lambda pid, sig, **kwargs: sent.append((pid, sig)) or True)
    result = tmux.terminate_window("dev", grace_seconds=0)
    assert sent == [(200, signal.SIGTERM), (100, signal.SIGTERM),
                    (200, signal.SIGKILL), (100, signal.SIGKILL)]
    assert result.alive_after_kill  # A surviving child is not reported as terminated.


def test_owned_pane_cwd_fallback_rejects_changed_binding(tree, monkeypatch, tmp_path):
    import subprocess
    folder = tmp_path / 'project'
    folder.mkdir()
    (tree / '100/cwd').symlink_to(folder)
    original = tmux_foreground.process_cwd
    monkeypatch.setattr(tmux_foreground, 'process_cwd', lambda pid: original(pid, proc_root=tree))
    tmux = TmuxSession('isolated')
    monkeypatch.setattr(tmux, '_target', lambda _: '%1')
    monkeypatch.setattr(tmux, '_run', lambda *args, **kwargs: subprocess.CompletedProcess([], 0, '', ''))
    monkeypatch.setattr(tmux, 'pane_pid', lambda _: '100')
    assert tmux.pane_current_path('dev') == str(folder)
    ids = iter(['100', '200'])
    monkeypatch.setattr(tmux, 'pane_pid', lambda _: next(ids))
    assert tmux.pane_current_path('dev') == ''


def test_cwd_fallback_rejects_process_reuse_or_missing_directory(tree, monkeypatch):
    (tree / '100/cwd').symlink_to(tree)
    original = tmux_foreground._read
    calls = []
    def read(pid, root):
        calls.append(pid)
        if len(calls) == 2:
            proc(tree, 100, name='bash', parent=50, started=11)
        return original(pid, root)
    monkeypatch.setattr(tmux_foreground, '_read', read)
    assert tmux_foreground.process_cwd('100', proc_root=tree) == ''
    (tree / '100/cwd').unlink()
    assert tmux_foreground.process_cwd('100', proc_root=tree) == ''
