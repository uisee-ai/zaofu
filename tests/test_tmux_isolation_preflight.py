"""An explicitly requested private tmux server must never silently fall back."""
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.runtime.tmux import TmuxError, TmuxSession, tmux_env


@pytest.mark.parametrize("kind", ["missing", "file", "unwritable"])
def test_invalid_explicit_tmux_directory_rejected_before_command(tmp_path, monkeypatch, kind):
    path = tmp_path / "private-tmux"
    if kind == "file":
        path.write_text("not a directory")
    if kind == "unwritable":
        path.mkdir()
        monkeypatch.setattr("zf.runtime.tmux.os.access", lambda *args: False)
    monkeypatch.setenv("TMUX_TMPDIR", str(path))
    calls = []

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("zf.runtime.tmux.subprocess.run", run)
    with pytest.raises(TmuxError, match="TMUX_TMPDIR"):
        TmuxSession(session_name="product").has_session()
    assert not calls


def test_valid_explicit_directory_is_preserved(tmp_path):
    assert tmux_env({"TMUX_TMPDIR": str(tmp_path), "TMUX": "old-socket"}) == {
        "TMUX_TMPDIR": str(tmp_path),
    }


@pytest.mark.host
def test_real_private_servers_with_same_session_name_stay_separate(monkeypatch, record_property):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory(prefix=f"zf-tmux-preflight-{stamp}-") as temporary:
        roots = [Path(temporary) / name for name in ("one", "two")]
        sockets = []
        created = []
        try:
            for root in roots:
                root.mkdir(mode=0o700)
                monkeypatch.setenv("TMUX_TMPDIR", str(root))
                session = TmuxSession(session_name="product")
                session.create_session()
                created.append((root, session))
                result = session._run(["tmux", "display-message", "-p", "-t", "=product",
                                       "#{socket_path}"], capture=True)
                socket = Path(result.stdout.strip())
                assert socket.is_relative_to(root)
                sockets.append(str(socket))
            assert len(set(sockets)) == 2
            monkeypatch.setenv("TMUX_TMPDIR", str(roots[0]))
            created[0][1].kill_session()
            assert not created[0][1].has_session()
            monkeypatch.setenv("TMUX_TMPDIR", str(roots[1]))
            assert created[1][1].has_session()
            record_property("isolated_socket_paths", sockets)
        finally:
            for root, session in reversed(created):
                monkeypatch.setenv("TMUX_TMPDIR", str(root))
                session.kill_session()
