"""Owned CLI process and tmux lifecycle for the browser General host tier."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import yaml

from tests.testkit.runner import _terminate
from zf.core.config.loader import load_config
from zf.core.state.session import SessionStore
from zf.runtime.tmux import TmuxSession
from zf.runtime.tmux_layout import PaneGridLayout, WindowPerRoleLayout


class LiveGeneralRuntime:
    def __init__(self, route, output: Path, repo: Path, *,
                 worker_module: str = "tests.testkit.tmux_general_worker") -> None:
        self.route, self.output = route, output
        self.process = None
        self.starts = 0
        self.pids: set[int] = set()
        self.session = f"zf-testkit-live-{uuid4().hex[:12]}"
        config_path = route.root / "zf.yaml"
        documents = list(yaml.safe_load_all(config_path.read_text()))
        for document in documents:
            if document["kind"] == "ZfConfig":
                document["spec"]["session"] = {"tmux_session": self.session}
        config_path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
        route.config = load_config(config_path)
        SessionStore(route.state / "session.yaml").create(project_root=str(route.root))
        executable = output / "mock-bin" / "cat"
        executable.parent.mkdir()
        executable.write_text(
            f"#!{sys.executable}\nimport os, sys\n"
            "if len(sys.argv) > 1 or not os.environ.get('ZF_ROLE_INSTANCE'):\n"
            "    os.execv('/bin/cat', ['/bin/cat', *sys.argv[1:]])\n"
            f"from {worker_module} import main\nmain()\n"
        )
        executable.chmod(0o755)
        self.env = {
            **os.environ, "PATH": f"{executable.parent}:{os.environ['PATH']}",
            "PYTHONPATH": f"{repo}:{repo / 'src'}", "PYTHONUNBUFFERED": "1",
            "ZF_CLI_CMD": f"{sys.executable} -m zf.cli.main",
        }

    def wait(self, predicate, *, seconds: float = 60) -> None:
        deadline = time.monotonic() + seconds
        while not predicate():
            assert self.process.poll() is None, self.log.read_text()
            assert time.monotonic() < deadline, self.log.read_text()
            time.sleep(0.1)

    def start(self, *, control_plane_only: bool = False) -> None:
        before = len(self.route.events("loop.started"))
        self.starts += 1
        self.log = self.output / f"runtime-{self.starts}.log"
        command = [sys.executable, "-m", "zf.cli.main"]
        if self.env.get("ZF_TESTKIT_RUNTIME_TIMING") == "1":
            command = [sys.executable, "-m", "tests.testkit.runtime_timing",
                       str(self.output / f"runtime-timing-{self.starts}.jsonl")]
        with self.log.open("w") as stream:
            self.process = subprocess.Popen(
                [*command, "start",
                 *(["--control-plane-only"] if control_plane_only else [])],
                cwd=self.route.root, env=self.env, stdout=stream, stderr=stream,
                start_new_session=True,
            )
        self.pids.add(self.process.pid)
        self.wait(lambda: len(self.route.events("loop.started")) > before)
        assert TmuxSession(self.session).has_session()

    def stop(self) -> None:
        if self.process is None:
            return
        layout = (PaneGridLayout(window_name="roles", binding_path=self.route.state / "pane_bindings.json")
                  if self.route.config.session.tmux_layout == "pane_grid" else WindowPerRoleLayout())
        tmux = TmuxSession(self.session, layout=layout)
        if tmux.has_session():
            for role in self.route.config.roles:
                probe = tmux.pane_process_probe(role.instance_id)
                if probe.get("pane_pid"):
                    (self.output / f"pane-{self.starts}-{role.instance_id}.log").write_text(
                        tmux.capture_pane(role.instance_id, lines=100),
                    )
                for pid in [probe.get("pane_pid"), *(row["pid"] for row in probe.get("processes", []))]:
                    if str(pid).isdigit():
                        self.pids.add(int(pid))
        try:
            stopped = subprocess.run(
                [sys.executable, "-m", "zf.cli.main", "stop", "--fast"],
                cwd=self.route.root, env=self.env, capture_output=True, text=True, timeout=30,
            )
            (self.output / f"stop-{self.starts}.log").write_text(stopped.stdout + stopped.stderr)
            assert stopped.returncode == 0, stopped.stdout + stopped.stderr
            self.process.wait(timeout=10)
        finally:
            _terminate(self.process)
            if tmux.has_session():
                tmux.kill_session()
        assert not tmux.has_session()
        assert not (self.route.state / "loop.lock").exists()
        deadline = time.monotonic() + 3
        while any(Path(f"/proc/{pid}").exists() for pid in self.pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not [pid for pid in self.pids if Path(f"/proc/{pid}").exists()]
        self.process = None
