"""Timing preserves real behavior and records nested calls and exceptions."""

from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.testkit.runtime_timing import CallTimer


@pytest.mark.parametrize("setting", [None, "0", "1"])
@pytest.mark.parametrize("control_plane_only", [False, True])
def test_live_runtime_timing_is_explicit_cli_opt_in(
    tmp_path, monkeypatch, setting, control_plane_only,
):
    from tests.testkit import live_general_runtime as live

    runtime = live.LiveGeneralRuntime.__new__(live.LiveGeneralRuntime)
    runtime.route = SimpleNamespace(root=tmp_path, events=lambda _: [])
    runtime.output, runtime.starts, runtime.session = tmp_path, 0, "isolated"
    runtime.env = {} if setting is None else {"ZF_TESTKIT_RUNTIME_TIMING": setting}
    runtime.pids = set()
    runtime.wait = lambda _: None
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(live.subprocess, "Popen", popen)
    monkeypatch.setattr(live, "TmuxSession", lambda _: SimpleNamespace(has_session=lambda: True))
    runtime.start(control_plane_only=control_plane_only)
    command, kwargs = calls.pop()
    prefix = [sys.executable, "-m", "zf.cli.main"]
    if setting == "1":
        prefix = [sys.executable, "-m", "tests.testkit.runtime_timing",
                  str(tmp_path / "runtime-timing-1.jsonl")]
    assert command == [*prefix, "start", *(["--control-plane-only"] if control_plane_only else [])]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"] is runtime.env
    assert kwargs["start_new_session"] is True
    assert runtime.pids == {12345}


def test_nested_call_return_identity_and_parent():
    stream = StringIO()
    timer = CallTimer(stream)
    value = object()
    child = timer.wrap("child", lambda item: item)
    parent = timer.wrap("parent", child)
    assert parent(value) is value
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row["name"] for row in rows] == ["child", "parent"]
    assert rows[0]["parent_id"] == rows[1]["call_id"]
    assert rows[1]["parent_id"] is None
    assert all(row["error"] is None and row["wall_seconds"] >= 0 for row in rows)
    assert rows[1]["wall_seconds"] >= rows[0]["wall_seconds"]


@pytest.mark.parametrize("error", [ValueError("same"), SystemExit(17)])
def test_exception_identity_and_completed_record(error):
    stream = StringIO()
    timer = CallTimer(stream)

    def fail():
        raise error

    with pytest.raises(type(error)) as raised:
        timer.wrap("failure", fail)()
    assert raised.value is error
    timer.wrap("after", lambda: None)()
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert rows[0]["error"] == type(error).__name__
    assert rows[1]["parent_id"] is None


def test_cli_passthrough_and_exclusive_diagnostic_output(tmp_path):
    output = tmp_path / "timing.jsonl"
    command = [sys.executable, "-m", "tests.testkit.runtime_timing", str(output), "--version"]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "zf" in result.stdout.lower()
    assert output.read_bytes() == b""  # Version does not drive the runtime.
    duplicate = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert duplicate.returncode != 0
    assert "FileExistsError" in duplicate.stderr


@pytest.mark.parametrize("fail", [False, True])
def test_main_instruments_host_seams_without_recording_arguments(tmp_path, monkeypatch, fail):
    from importlib import import_module
    from tests.testkit import runtime_timing
    from zf.runtime.tmux import TmuxSession

    cli = import_module("zf.cli.main")
    original = TmuxSession._run
    output = tmp_path / "timing.jsonl"
    monkeypatch.setattr(sys, "argv", ["timer", str(output), "start"])
    secret = "do-not-record-provider-input"
    error = ValueError("original failure")

    def run_cli():
        assert sys.argv == ["zf", "start"]
        result = TmuxSession(dry_run=True)._run(["tmux", secret])
        assert result.returncode == 0
        assert result.args == ["tmux", secret]
        if fail:
            raise error
        return 17

    monkeypatch.setattr(cli, "main", run_cli)
    with pytest.raises(ValueError if fail else SystemExit) as raised:
        runtime_timing.main()
    if fail:
        assert raised.value is error
    else:
        assert raised.value.code == 17
    assert TmuxSession._run is original
    assert secret not in output.read_text()
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["name"] == "_run"
    assert rows[0]["parent_id"] is None


@pytest.mark.parametrize("fail", [False, True])
def test_handshake_trace_preserves_cli_result_and_omits_screen_and_commands(
    tmp_path, monkeypatch, fail,
):
    from importlib import import_module
    from itertools import count
    import shlex

    from tests.testkit import runtime_timing
    from zf.core.config.schema import RoleConfig
    from zf.runtime.tmux import TmuxError, TmuxSession
    from zf.runtime.transport import TmuxTransport

    output = tmp_path / "handshake.jsonl"
    monkeypatch.setattr(sys, "argv", ["timer", str(output), "start"])
    secret = "private-screen-and-provider-command"
    screen, sent = [], []
    original_handshake = TmuxTransport._wait_for_shell_handshake
    clock = count()
    monkeypatch.setattr("zf.runtime.tmux_readiness.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("zf.runtime.tmux_readiness.time.sleep", lambda _: None)
    monkeypatch.setattr(TmuxSession, "pane_alive", lambda *args: True)
    monkeypatch.setattr(TmuxSession, "pane_current_command", lambda *args: "bash")
    monkeypatch.setattr(TmuxSession, "capture_pane", lambda *args, **kwargs: "\n".join(screen))
    monkeypatch.setattr(TmuxTransport, "_pane_process_probe", lambda *args: {"command": secret})

    def send(_self, _role, text, **kwargs):
        sent.append(text)
        if text.startswith("printf"):
            parts = shlex.split(text)
            assert parts[2] == "__ZF_SHELL_READY_"
            if not fail:
                screen.append("".join(parts[2:]))
            screen.append(secret)

    monkeypatch.setattr(TmuxSession, "send_keys", send)
    transport = TmuxTransport(TmuxSession())
    role = RoleConfig(name="judge", instance_id="judge-1", backend="mock")

    def cli():
        # Like zf start, consume the transport error before the outer wrapper
        # sees it: diagnostics must be recorded at the transport seam.
        try:
            transport._wait_for_shell_handshake(role)
        except TmuxError as exc:
            assert fail and exc.failure_class == "shell_handshake_timeout"
            assert secret in exc.last_screen_excerpt
            return 1
        return 0

    monkeypatch.setattr(import_module("zf.cli.main"), "main", cli)
    with pytest.raises(SystemExit) as caught:
        runtime_timing.main()
    assert caught.value.code == int(fail)
    assert TmuxTransport._wait_for_shell_handshake is original_handshake
    assert len(sent) == (2 if fail else 1)
    raw = output.read_text()
    assert secret not in raw and "printf" not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    [handshake] = [row for row in rows if row["name"] == "_wait_for_shell_handshake"]
    assert handshake["error"] == ("TmuxError" if fail else None)
    captures = [row for row in rows if row["name"] == "capture_pane"]
    assert captures and all(row["screen_chars"] > 0 for row in captures)
    [submission] = [row for row in rows if row["name"] == "send_keys" and row.get("shell_nonce")]
    assert all((submission["shell_nonce"] in row["shell_markers"]) == (not fail) for row in captures)
    if fail:
        [diagnostic] = [row for row in rows if row["name"] == "_record_readiness_failure"]
        assert diagnostic["readiness"] == {
            "failure_class": "shell_handshake_timeout", "pane_alive": True,
            "shell_submit_attempts": 2, "launch_attempts": 0,
        }
