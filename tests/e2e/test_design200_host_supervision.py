"""Real user-systemd and tmux lifecycle; no model/provider quality claim."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import pytest
import yaml

from zf.core.events import EventLog
from zf.core.state.session import SessionStore

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[2]


def _until(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError("host lifecycle condition not observed")


@pytest.mark.parametrize("resident_enabled", [False, True])
def test_host_recovers_unique_runtime_and_respects_pause(resident_enabled):
    if not shutil.which("tmux") or not shutil.which("systemd-run"):
        pytest.skip("tmux and user systemd required")
    if subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True).returncode:
        pytest.skip("user systemd is not running")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    root = Path(tempfile.mkdtemp(prefix=f"zf-200-host-{stamp}-"))
    state = root / ".test-state"
    state.mkdir()
    (root / "tmux").mkdir()
    unit = "zf-200-host-" + root.name[-8:]
    session = unit + "-workers"
    config = {"version": "1", "project": {"name": unit, "state_dir": ".test-state"},
              "session": {"tmux_session": session},
              "runtime": {"run_manager": {"enabled": False}, "autoresearch_resident": {
                  "enabled": resident_enabled, "max_actions_per_tick": 0,
                  "worktree_root": str(root / "repair-worktrees")}},
              "roles": [{"name": "worker", "backend": "mock"}]}
    (root / "zf.yaml").write_text(yaml.safe_dump(config))
    SessionStore(state / "session.yaml").create(project_root=str(root))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "ZF_PROJECT_ROOT": str(root),
           "ZF_STATE_DIR": str(state), "TMUX_TMPDIR": str(root / "tmux")}
    env.pop("ZF_TMUX_SESSION", None)
    cli = [sys.executable, "-m", "zf.cli.main"]
    calls = []

    def run(args, check=True):
        result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=45)
        calls.append({"command": args, "returncode": result.returncode, "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]})
        if check:
            assert result.returncode == 0, calls[-1]
        return result

    def pid():
        result = run(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], check=False)
        return int(result.stdout.strip() or 0) if result.returncode == 0 else 0

    def loops():
        return [e for e in EventLog(state / "events.jsonl").read_all() if e.type == "loop.started"]

    try:
        run(cli + ["start", "--no-watch", "--skip-workflow-inspect"])
        command = ["systemd-run", "--user", "--unit", unit, "--property", "Type=simple",
            "--property", "Restart=on-failure", "--property", "RestartSec=1s", "--property", "KillMode=process",
            "--property", f"WorkingDirectory={root}", "--property", f"ExecCondition={sys.executable} -m zf.cli.main recover host-check --state-dir {state}"]
        for key in ("PATH", "PYTHONPATH", "ZF_PROJECT_ROOT", "ZF_STATE_DIR", "TMUX_TMPDIR"):
            command += ["--setenv", f"{key}={env[key]}"]
        run(command + cli + ["start", "--control-plane-only", "--skip-workflow-inspect"])
        _until(lambda: len(loops()) == 1)
        first = pid()
        assert first > 1
        sidecar_path = state / "processes" / "autoresearch-resident.pid.json"
        sidecar_pid = json.loads(sidecar_path.read_text())["pid"] if resident_enabled else None
        os.kill(first, signal.SIGKILL)
        _until(lambda: len(loops()) == 2)
        second = pid()
        assert second > 1 and first != second
        guard = json.loads((state / "processes" / "watcher.pid.json").read_text())
        assert guard["owner_pid"] == second
        assert all(e.payload.get("runtime_load_ref") for e in loops())
        if resident_enabled:
            assert json.loads(sidecar_path.read_text())["pid"] == sidecar_pid
            started = [e for e in EventLog(state / "events.jsonl").read_all()
                       if e.type == "autoresearch.resident_sidecar.started"]
            assert len(started) == 1, "control-plane restart duplicated a live resident"
        run(cli + ["emit", "loop.pause_requested", "--actor", "operator"])
        os.kill(second, signal.SIGKILL)
        _until(lambda: pid() == 0)
        time.sleep(2)
        assert len(loops()) == 2 and pid() == 0
        if resident_enabled:
            from zf.runtime.autoresearch_resident_sidecar import _process_group_alive

            run(cli + ["stop", "--include-run-manager"])
            assert not _process_group_alive(sidecar_pid), "normal stop left its resident tree alive"
    finally:
        run(["systemctl", "--user", "stop", unit], check=False)
        run(cli + ["emit", "simulation.done", "--actor", "operator"], check=False)
        run(cli + ["stop", "--include-run-manager"], check=False)
        # Clean even a red regression's superseded pidfile owners. Only this
        # isolated test's recorded processes and exact state dir are eligible.
        for event in EventLog(state / "events.jsonl").read_all():
            if event.type != "autoresearch.resident_sidecar.started":
                continue
            child = int(event.payload.get("pid") or 0)
            try:
                command = Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")
                if str(state).encode() in command and os.getpgid(child) == child:
                    os.killpg(child, signal.SIGTERM)
            except OSError:
                pass
        run(["tmux", "kill-session", "-t", session], check=False)
        run(["systemctl", "--user", "reset-failed", unit], check=False)
        evidence = Path("/tmp/zf-200-verification-20261106")
        evidence.mkdir(exist_ok=True)
        receipt = "sidecar-host-supervision.json" if resident_enabled else "host-supervision.json"
        sidecar_events = [json.loads(e.to_json()) for e in EventLog(state / "events.jsonl").read_all()
                          if e.type.startswith("autoresearch.resident_sidecar.")]
        (evidence / receipt).write_text(json.dumps({"root": str(root), "unit": unit,
            "loops": [json.loads(e.to_json()) for e in loops()], "sidecar_events": sidecar_events,
            "calls": calls}, ensure_ascii=False, indent=2))
        shutil.rmtree(root)
