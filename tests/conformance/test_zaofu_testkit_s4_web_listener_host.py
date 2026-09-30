"""Explicit host checks for an owned API listener and SSE disconnect cleanup."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import httpx
import pytest

from tests.testkit.cleanup import terminate_process, terminate_process_group
from tests.testkit.runner import run_pytest
from tests.testkit.workloop_resources import observe_process, observe_tcp_sockets
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task import Task, TaskStore


def _project(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    state = project / "runtime-state"
    state.mkdir(parents=True)
    (project / "zf.yaml").write_text(
        "project:\n  name: listener-host\n  state_dir: runtime-state\n"
        "roles: []\nworkflow:\n  dag:\n    stage_order: [tasks]\n",
        encoding="utf-8",
    )
    SessionStore(state / "session.yaml").create(project_root=str(project))
    TaskStore(state / "kanban.json").add(Task(id="LISTENER-TASK", title="Listener fixture"))
    EventWriter(EventLog(state / "events.jsonl")).append(
        ZfEvent(type="task.created", actor="testkit", task_id="LISTENER-TASK"),
    )
    return project, state


def _start_api(project: Path, output: Path) -> tuple[subprocess.Popen[str], int]:
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "ZF_TESTKIT_BROWSER_ROOT": str(project),
        "ZF_TESTKIT_BROWSER_DIST": str(output / "dist"),
        "ZF_WEB_ACTION_TOKEN": "listener-host-token",
        "ZF_WORKSPACE_HOME": str(output / "workspace"),
    }
    for name in ("ZF_WEB_TRUSTED_SESSION", "ZF_WEB_PASSCODE"):
        env.pop(name, None)
    # Pass the already-bound listener; do not race by releasing a "free port".
    with socket.socket() as listener, (output / "api.log").open("w") as log:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        assert port > 8001
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tests.testkit.browser_app:create_app", "--factory",
             "--fd", str(listener.fileno()), "--timeout-graceful-shutdown", "1"],
            cwd=Path(__file__).resolve().parents[2], env=env, stdout=log, stderr=log,
            pass_fds=(listener.fileno(),), text=True,
            # Inherit the TestKit pytest process group for outer-timeout cleanup.
            start_new_session=False,
        )
    return process, port


def _wait_ready(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 20
    with httpx.Client(trust_env=False, timeout=1) as client:
        while time.monotonic() < deadline:
            assert process.poll() is None
            try:
                if client.get(f"http://127.0.0.1:{port}/api/state").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.05)
    raise AssertionError("owned API listener did not become ready")


def _port_closed(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _await_disconnected(pid):
    deadline = time.monotonic() + 3
    while True:
        rows = observe_tcp_sockets(pid)
        if all(row["state"] == "0A" for row in rows):
            assert rows, "API listener disappeared"
            return observe_process(pid)
        assert time.monotonic() < deadline, rows
        time.sleep(0.05)


@pytest.fixture
def listener_scope():
    with TemporaryDirectory(prefix="zf-k552-api-20260925-") as root:
        yield Path(root)


def _finish(api, port, state):
    try:
        log = EventLog(state / "events.jsonl")
        writer = EventWriter(log)
        writer.append(ZfEvent(type="simulation.done", actor="testkit"))
        log.close()
    finally:
        terminate_process(api, timeout=3)
    assert not Path(f"/proc/{api.pid}").exists()
    assert _port_closed(port)
    # Closed listener is genuinely reusable, not merely refusing requests.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


@pytest.mark.host
@pytest.mark.serial
@pytest.mark.parametrize("exit_mode", ["normal", "assertion"])
def test_owned_api_listener_and_sse_disconnect_return_to_baseline(
    listener_scope, record_property, exit_mode,
):
    project, state = _project(listener_scope)
    output = listener_scope / "evidence"
    output.mkdir()
    api, port = _start_api(project, output)
    try:
        _wait_ready(api, port)
        ready = _await_disconnected(api.pid)
        cycles = []
        fd_ceiling = None
        with httpx.Client(trust_env=False, timeout=5) as client:
            for _ in range(8):
                with client.stream("GET", f"http://127.0.0.1:{port}/api/stream") as response:
                    assert response.status_code == 200
                    lines = response.iter_lines()
                    assert next(lines) == ": connected"
                    connected = observe_process(api.pid)
                    assert len(connected["socket_inodes"]) > len(ready["socket_inodes"])
                    assert any(row["state"] == "01" for row in observe_tcp_sockets(api.pid))
                after = _await_disconnected(api.pid)
                assert after["socket_inodes"] == ready["socket_inodes"]
                if fd_ceiling is None:
                    fd_ceiling = after["fds"]
                else:
                    assert after["fds"] <= fd_ceiling
                cycles.append({"connected": connected, "after_disconnect": after})
        record_property("listener_lifecycle", json.dumps({
            "port": port, "ready": ready, "cycles": cycles,
        }))
        if exit_mode == "assertion":
            # Exercise the same finally path without swallowing a real assertion.
            with pytest.raises(AssertionError, match="injected caller failure"):
                try:
                    raise AssertionError("injected caller failure")
                finally:
                    _finish(api, port, state)
    finally:
        _finish(api, port, state)
        record_property("listener_cleanup", json.dumps({"pid": api.pid, "port": port, "clean": True}))


@pytest.mark.host
@pytest.mark.serial
def test_owned_api_listener_sigkill_releases_port(listener_scope, record_property):
    project, state = _project(listener_scope)
    output = listener_scope / "evidence"
    output.mkdir()
    api, port = _start_api(project, output)
    try:
        _wait_ready(api, port)
        ready = observe_process(api.pid)
        os.kill(api.pid, signal.SIGKILL)
        api.wait(timeout=5)
        assert _port_closed(port)
        record_property("listener_sigkill", json.dumps({"port": port, "ready": ready}))
    finally:
        _finish(api, port, state)
        record_property("listener_cleanup", json.dumps({"pid": api.pid, "port": port, "clean": True}))


@pytest.mark.host
@pytest.mark.serial
@pytest.mark.parametrize("mode", ["timeout", "runner-sigkill"])
def test_outer_runner_cleans_api_after_test_process_exit(
    listener_scope, monkeypatch, record_property, mode, request,
):
    root = listener_scope / "inner"
    root.mkdir()
    marker = root / "api-owner.json"
    monkeypatch.setenv("ZF_LISTENER_FAILURE_ROOT", str(root))
    monkeypatch.setenv("ZF_LISTENER_FAILURE_MODE", mode)
    real_popen = subprocess.Popen
    owners = []

    def synchronized_popen(command, *args, **kwargs):
        process = real_popen(command, *args, **kwargs)
        if list(command[:3]) != [sys.executable, "-m", "pytest"]:
            return process
        # Safety cleanup runs after assertions, so it cannot satisfy the detector.
        request.addfinalizer(lambda: terminate_process_group(process, timeout=1))
        try:
            deadline = time.monotonic() + 30
            while not marker.exists():
                assert process.poll() is None, "inner pytest exited before API readiness"
                assert time.monotonic() < deadline, "inner API startup timeout"
                time.sleep(0.05)
            owner = json.loads(marker.read_text())
            assert owner["pgid"] == process.pid
            owners.append(owner)
            return process
        except BaseException:
            terminate_process_group(process, timeout=1)
            raise

    monkeypatch.setattr(subprocess, "Popen", synchronized_popen)
    report = run_pytest(
        project_root=Path(__file__).resolve().parents[2],
        output_dir=listener_scope / "inner-report", timeout_seconds=1,
        nodeids=("tests/testkit/fixtures/web_listener_failure_fixture.py",),
    )
    [owner] = owners
    assert report.verdict == "harness_failure"
    assert report.timed_out is (mode == "timeout")
    assert not report.session_finished
    assert report.exit_code == (-signal.SIGTERM if mode == "timeout" else -signal.SIGKILL)
    deadline = time.monotonic() + 5
    while any(Path(f"/proc/{pid}").exists() for pid in (owner["pid"], owner["api_pid"])) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not Path(f"/proc/{owner['pid']}").exists()
    assert not Path(f"/proc/{owner['api_pid']}").exists()
    assert _port_closed(owner["port"])
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", owner["port"]))
    EventWriter(EventLog(root / "project/runtime-state/events.jsonl")).append(
        ZfEvent(type="simulation.done", actor="testkit"),
    )
    record_property("outer_listener_cleanup", json.dumps({
        **owner, "mode": mode, "verdict": report.verdict,
        "exit_code": report.exit_code, "timed_out": report.timed_out,
        "pid_gone": True, "port_reusable": True,
    }))
