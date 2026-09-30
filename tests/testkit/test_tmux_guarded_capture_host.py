"""Explicit isolated-host guard and production capture/resolver contracts."""

from __future__ import annotations

from contextlib import contextmanager
import os
import shlex
import shutil
import subprocess
import tempfile
import time

import pytest

from zf.runtime.tmux import tmux_env


@contextmanager
def isolated_tmux():
    if not shutil.which("tmux"):
        pytest.skip("explicit host probe requires tmux")
    with tempfile.TemporaryDirectory(prefix="zf-k579-capture-20260928-") as directory:
        socket = f"{directory}/tmux.sock"

        def run(*args, check=True):
            return subprocess.run(
                ["tmux", "-S", socket, *args], capture_output=True,
                text=True, timeout=10, check=check, env=tmux_env(),
            )

        server_pid = None
        try:
            pane = run("new-session", "-d", "-P", "-F", "#{pane_id}",
                       "-s", "probe", "-x", "80", "-y", "20", "cat").stdout.strip()
            server_pid = int(run("display-message", "-p", "#{pid}").stdout.strip())
            run("set-option", "-p", "-t", pane, "@zf_instance_id", "dev")
            # Wait for both terminal echo and cat output before comparing
            # windows; send-keys completing does not mean output is drained.
            rows = [f"line-{i:03}" for i in range(260)]
            rows[130] = "Codex ran out of room in the model's context window."
            run("send-keys", "-l", "-t", pane, "\n".join(rows) + "\n")
            deadline = time.monotonic() + 5
            while run("capture-pane", "-p", "-t", pane, "-S", "-600").stdout.count("line-259") < 2:
                assert time.monotonic() < deadline, "isolated pane output did not settle"
                time.sleep(0.01)
            yield run, pane
        finally:
            run("kill-server", check=False)
            assert run("list-sessions", check=False).returncode != 0
            if server_pid is not None:
                # kill-server is synchronous from the client perspective;
                # process-table disappearance may lag, so do not kill by PID.
                assert server_pid != os.getpid()


@pytest.mark.host
@pytest.mark.parametrize("lines", [120, 200])
@pytest.mark.parametrize("identity", ["owned", "foreign_owner", "wrong_pane"])
def test_server_guard_preserves_capture_window_and_prevents_foreign_read(lines, identity):
    with isolated_tmux() as (run, pane):
        baseline = run("capture-pane", "-p", "-t", pane, "-S", str(-lines)).stdout
        if identity == "foreign_owner":
            run("set-option", "-p", "-t", pane, "@zf_instance_id", "foreign")
        expected_pane = "%999999" if identity == "wrong_pane" else pane
        guard = "#{&&:#{==:#{pane_id}," + expected_pane + "},#{==:#{@zf_instance_id},dev}}"
        # The witness distinguishes "not executed" from "executed then
        # discarded". Only this private test server's option is modified.
        command = shlex.join(["set-option", "-p", "-t", pane, "@capture_executed", "yes"])
        command += " ; " + shlex.join(["capture-pane", "-p", "-t", pane, "-S", str(-lines)])
        result = run("if-shell", "-F", "-t", pane, guard, command,
                     "display-message -p rejected")
        executed = run("show-options", "-p", "-v", "-t", pane,
                       "@capture_executed", check=False)
        if identity == "owned":
            assert executed.stdout.strip() == "yes"
            assert result.stdout == baseline
        else:
            assert result.stdout == "rejected\n"
            assert executed.returncode != 0


@pytest.mark.host
@pytest.mark.parametrize("lines", [120, 200])
@pytest.mark.parametrize("identity", ["owned", "foreign_owner", "wrong_pane"])
def test_tmux_session_capture_pane_guarded_caller(identity, lines):
    """Exercise the production capture entrypoint on the isolated server."""
    from zf.runtime.tmux import TmuxError, TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout

    with isolated_tmux() as (run, pane):
        layout = PaneGridLayout(window_name="roles")
        layout._panes["dev"] = pane
        session = TmuxSession("probe", layout=layout)

        calls = []

        def invoke(args, *, check=True, capture=False):
            calls.append(args)
            try:
                result = run(*args[1:], check=check)
            except subprocess.CalledProcessError as exc:
                raise TmuxError(str(exc)) from exc
            return result

        session._run = invoke
        if identity == "foreign_owner":
            run("set-option", "-p", "-t", pane, "@zf_instance_id", "foreign")
        elif identity == "wrong_pane":
            layout._panes["dev"] = "%999999"

        if identity == "owned":
            baseline = run("capture-pane", "-p", "-t", pane, "-S", str(-lines)).stdout
            output = session.capture_pane("dev", lines=lines)
            assert output == baseline
            assert "line-" in output
            assert "Codex ran out of room" in output
            assert len(calls) == 1
        else:
            with pytest.raises(TmuxError):
                session.capture_pane("dev", lines=lines)
            assert layout._panes.get("dev") is None


@pytest.mark.host
@pytest.mark.parametrize("old_pane", ["foreign", "gone"])
def test_capture_rediscovers_real_replacement_in_same_call(old_pane):
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout

    with isolated_tmux() as (run, pane):
        layout = PaneGridLayout()
        layout._panes["dev"] = pane
        layout._window_id = run("display-message", "-p", "-t", pane, "#{window_id}").stdout.strip()
        session = TmuxSession("probe", layout=layout)
        session._run = lambda args, **kwargs: run(*args[1:], check=kwargs.get("check", True))
        replacement = run("split-window", "-d", "-P", "-F", "#{pane_id}",
                          "-t", pane, "sleep 3600").stdout.strip()
        run("set-option", "-p", "-t", replacement, "@zf_instance_id", "dev")
        run("set-option", "-p", "-t", pane, "@zf_instance_id", "foreign")
        if old_pane == "gone":
            run("kill-pane", "-t", pane)
        baseline = run("capture-pane", "-p", "-t", replacement, "-S", "-200").stdout
        assert session.capture_pane("dev", lines=200) == baseline
        assert layout._panes["dev"] == replacement


@pytest.mark.host
@pytest.mark.parametrize("identity", ["owned", "foreign", "gone"])
def test_liveness_snapshot_real_tmux_preserves_status_and_screen(identity):
    from zf.runtime.tmux import TmuxError, TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout

    with isolated_tmux() as (run, pane):
        layout = PaneGridLayout()
        layout._panes["dev"] = pane
        session = TmuxSession("probe", layout=layout)
        calls = []

        def invoke(args, **kwargs):
            calls.append(args)
            try:
                return run(*args[1:], check=kwargs.get("check", True))
            except subprocess.CalledProcessError as exc:
                raise TmuxError(str(exc)) from exc

        session._run = invoke
        expected_status = run("display-message", "-p", "-t", pane,
                              "#{pane_pid}\t#{pane_current_command}").stdout.strip().split("\t")
        expected_screen = run("capture-pane", "-p", "-t", pane, "-S", "-120").stdout
        if identity == "foreign":
            run("set-option", "-p", "-t", pane, "@zf_instance_id", "foreign")
        elif identity == "gone":
            layout._panes["dev"] = "%999999"
        result = session.pane_liveness_snapshot("dev")
        assert len(calls) == 1
        if identity == "owned":
            assert result == (*expected_status, expected_screen)
        else:
            assert result is None


@pytest.mark.host
def test_liveness_snapshot_rejection_preserves_transport_rediscovery():
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout
    from zf.runtime.transport import TmuxTransport

    with isolated_tmux() as (run, pane):
        layout = PaneGridLayout()
        layout._panes["dev"] = pane
        layout._window_id = run("display-message", "-p", "-t", pane, "#{window_id}").stdout.strip()
        session = TmuxSession("probe", layout=layout)
        session._run = lambda args, **kwargs: run(*args[1:], check=kwargs.get("check", True))
        replacement = run("split-window", "-d", "-P", "-F", "#{pane_id}",
                          "-t", pane, "sleep 3600").stdout.strip()
        run("set-option", "-p", "-t", pane, "@zf_instance_id", "foreign")
        run("set-option", "-p", "-t", replacement, "@zf_instance_id", "dev")
        assert TmuxTransport(session).is_alive("dev")
        assert layout._panes["dev"] == replacement


@pytest.mark.host
def test_paired_liveness_and_log_snapshot_preserves_both_real_windows():
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout

    with isolated_tmux() as (run, pane):
        layout = PaneGridLayout()
        layout._panes["dev"] = pane
        session = TmuxSession("probe", layout=layout)
        calls = []

        def invoke(args, **kwargs):
            calls.append(args)
            return run(*args[1:], check=kwargs.get("check", True))

        session._run = invoke
        expected_120 = run("capture-pane", "-p", "-t", pane, "-S", "-120").stdout
        expected_200 = run("capture-pane", "-p", "-t", pane, "-S", "-200").stdout
        result = session.pane_liveness_and_log_snapshot("dev")
        assert result is not None
        assert result[0].isdigit() and result[1]
        assert result[2] == expected_120
        assert result[3] == expected_200
        assert len(calls) == 1
        command = " ".join(calls[0])
        assert "-S -120" in command and "-S -200" in command
        assert command.count("if-shell") >= 3  # outer + one guard per window


@pytest.mark.host
def test_paired_liveness_and_log_snapshot_rejects_foreign_owner():
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout

    with isolated_tmux() as (run, pane):
        layout = PaneGridLayout()
        layout._panes["dev"] = pane
        session = TmuxSession("probe", layout=layout)
        session._run = lambda args, **kwargs: run(*args[1:], check=kwargs.get("check", True))
        run("set-option", "-p", "-t", pane, "@zf_instance_id", "foreign")
        assert session.pane_liveness_and_log_snapshot("dev") is None


@pytest.mark.host
def test_paired_second_guard_rejects_owner_change_between_windows():
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout

    with isolated_tmux() as (run, pane):
        layout = PaneGridLayout()
        layout._panes["dev"] = pane
        session = TmuxSession("probe", layout=layout)
        responses = []

        def invoke(args, **kwargs):
            # Insert the race at the exact seam on the private server.
            args = list(args)
            head, separator, tail = args[6].rpartition(" ; if-shell")
            assert separator
            change_owner = shlex.join([
                "set-option", "-p", "-t", pane, "@zf_instance_id", "foreign",
            ])
            args[6] = head + " ; " + change_owner + separator + tail
            result = run(*args[1:], check=kwargs.get("check", True))
            responses.append(result.stdout)
            return result

        session._run = invoke
        assert session.pane_liveness_and_log_snapshot("dev") is None
        assert "__ZF_CAPTURE_120_OK__\n" in responses[0]
        assert "__ZF_CAPTURE_200_OK__\n" not in responses[0]
        assert "__ZF_CAPTURE_200_REJECTED__\n" in responses[0]


@pytest.mark.host
def test_paired_marker_in_real_pane_requests_lossless_legacy_capture():
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.tmux_layout import PaneGridLayout

    with isolated_tmux() as (run, pane):
        marker = "__ZF_CAPTURE_200_OK__"
        run("send-keys", "-l", "-t", pane, marker + "\n")
        deadline = time.monotonic() + 5
        while run("capture-pane", "-p", "-t", pane, "-S", "-200").stdout.count(marker) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        layout = PaneGridLayout()
        layout._panes["dev"] = pane
        session = TmuxSession("probe", layout=layout)
        session._run = lambda args, **kwargs: run(*args[1:], check=kwargs.get("check", True))
        baseline = run("capture-pane", "-p", "-t", pane, "-S", "-200").stdout
        assert session.pane_liveness_and_log_snapshot("dev") is None
        assert session.capture_pane("dev", lines=200) == baseline
