"""Host proof for CLI stop modes, process cleanup and session isolation."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import (
    _assert_gone,
    _live_pane,
    _process_ids,
)
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.state.session import SessionStore
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.tmux import TmuxSession


@pytest.mark.host
@pytest.mark.serial
@pytest.mark.parametrize("mode", ["fast", "graceful", "force"])
@pytest.mark.parametrize("stubborn", [False, True])
def test_cli_fast_stop_removes_detached_tool_descendant(stop_project, record_property, mode, stubborn):
    """ZF-TEST-108: a tool in a new session must not outlive its owning pane."""
    if shutil.which("tmux") is None or not Path("/proc").is_dir():
        pytest.skip("requires Linux and tmux")
    project = stop_project
    state = project / "runtime-state"
    state.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="detached-stop", state_dir="runtime-state"),
        session=SessionConfig(tmux_session=f"zf-k553-stop-{uuid.uuid4().hex}"),
        roles=[RoleConfig(name="dev", instance_id="dev-1", backend="mock")],
    )
    (project / "zf.yaml").write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
    SessionStore(state / "session.yaml").create(project_root=str(project))
    tmux = TmuxSession(config.session.tmux_session)
    ready = project / "tool.json"
    # The fake provider waits/reaps the tool, while the tool owns its own SID,
    # just like the actual Claude Bash subprocess observed in K553.
    child_code = "import time,signal;"
    if stubborn:
        child_code += "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
    child_code += f"from pathlib import Path;Path({str(project / 'child-ready')!r}).touch();time.sleep(120)"
    code = (
        "import subprocess,sys,json;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],start_new_session=True);"
        f"Path({str(ready)!r}).write_text(json.dumps({{'pid':p.pid}}));p.wait()"
    )
    pid = None
    birth = None
    witness = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"], start_new_session=True)
    try:
        tmux.create_session()
        tmux.create_window("dev-1")
        tmux.send_keys("dev-1", shlex.join([sys.executable, "-c", code]), submit_delay_s=0)
        deadline = time.monotonic() + 10
        while not ready.exists() or not (project / "child-ready").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        pid = json.loads(ready.read_text())["pid"]
        birth = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        assert os.getsid(pid) == pid
        probe = tmux.pane_process_probe("dev-1", max_entries=64)
        assert str(pid) in {row["pid"] for row in probe["processes"]}
        result = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", "stop", "--include-run-manager",
             *((f"--{mode}",) if mode != "graceful" else ())],
            cwd=project, capture_output=True, text=True, timeout=30,
        )
        alive = Path(f"/proc/{pid}").exists()
        record_property("detached_tool_stop", json.dumps({
            "mode": mode, "stubborn": stubborn, "pid": pid, "start_ticks": birth, "owned_before_stop": probe,
            "exit_code": result.returncode, "session_gone": not tmux.has_session(),
            "tool_exists_after_stop": alive,
        }))
        assert result.returncode == 0, result.stderr
        assert not tmux.has_session()
        _assert_gone({str(pid)})
        assert witness.poll() is None
    finally:
        EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
            type="simulation.done", actor="testkit", payload={"reason": "detached-tool-stop"},
        ))
        # Cleanup only after retaining the failure. Revalidate birth identity,
        # then pidfd signaling cannot accidentally target a reused numeric PID.
        from tests.testkit.native_pipeline_recovery import _kill_owned_provider
        if pid is not None and Path(f"/proc/{pid}/stat").exists():
            current = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            assert current == birth
            _kill_owned_provider(pid)
        tmux.kill_session()
        witness.terminate()
        witness.wait(timeout=5)
        if pid is not None:
            _assert_gone({str(pid)})


@pytest.fixture
def stop_project():
    with TemporaryDirectory(prefix="zf-k479-stop-20260923-") as directory:
        yield Path(directory)


@pytest.mark.host
@pytest.mark.serial
@pytest.mark.parametrize("mode", ["force", "fast", "graceful"])
def test_cli_stop_removes_only_owned_tmux_session(stop_project, record_property, mode) -> None:
    if shutil.which("tmux") is None:
        pytest.skip("host tier requires tmux")
    project = stop_project
    state = project / "runtime-state"
    state.mkdir()
    session_name = f"zf-k479-stop-{uuid.uuid4().hex}"
    config = ZfConfig(
        project=ProjectConfig(name="k479-stop", state_dir="runtime-state"),
        session=SessionConfig(tmux_session=session_name),
        roles=[RoleConfig(name="dev", instance_id="dev-1", backend="mock")],
    )
    (project / "zf.yaml").write_text(
        yaml.safe_dump(renderable_config_to_primitive(config)), encoding="utf-8",
    )
    SessionStore(state / "session.yaml").create(project_root=str(project))
    lock = state / "loop.lock"
    lock.write_text("owned-by-test\n", encoding="utf-8")
    tmux = TmuxSession(session_name)
    witness = TmuxSession(f"zf-k479-witness-{uuid.uuid4().hex}")
    owned_pids: set[str] = set()
    witness_pids: set[str] = set()
    try:
        for session in (tmux, witness):
            session.create_session()
            session.create_window("dev-1")
            session.send_keys("dev-1", "cat", submit_delay_s=0.0)
            _live_pane(session, "dev-1")
        owned_pids = _process_ids(tmux, "dev-1")
        witness_pids = _process_ids(witness, "dev-1")
        argv = [sys.executable, "-m", "zf.cli.main", "stop", "--include-run-manager"]
        if mode != "graceful":
            argv.append(f"--{mode}")
        for _ in range(2):
            # A fresh CLI process must resolve the project, not inherit fixture objects.
            stopped = subprocess.run(
                argv, cwd=project, capture_output=True, text=True, timeout=30, check=False,
            )
            assert stopped.returncode == 0, stopped.stderr
            assert not tmux.has_session()
            _assert_gone(owned_pids)
            assert not lock.exists()
            assert witness.has_session()
            assert _process_ids(witness, "dev-1") == witness_pids
        events = EventLog(state / "events.jsonl").read_all()
        if mode != "force":
            assert SessionStore(state / "session.yaml").load().runtime_state == "stopped"
            assert any(e.type == "loop.stopped" for e in events)
        if mode == "fast":
            assert any(e.type == "run.teardown" for e in events)
        record_property("cli_stop", json.dumps({
            "mode": mode, "argv": argv, "session": session_name,
            "owned_pids": sorted(owned_pids), "unaffected_pids": sorted(witness_pids),
            "repeat_exit_codes": [0, 0], "lock_removed": not lock.exists(),
        }))
    finally:
        try:
            EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(
                type="simulation.done", actor="testkit", payload={"reason": "host-stop-cleanup"},
            ))
        finally:
            tmux.kill_session()
            witness.kill_session()
            assert not tmux.has_session() and not witness.has_session()
            _assert_gone(owned_pids | witness_pids)
