from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    RuntimeAutoresearchResidentConfig,
    RuntimeConfig,
    ZfConfig,
)
from zf.runtime.autoresearch_resident_sidecar import (
    build_autoresearch_resident_command,
    start_autoresearch_resident_sidecar,
    stop_autoresearch_resident_sidecar,
)


class EventSink:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


class FakeProcess:
    def __init__(self, pid: int = 4312) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_build_autoresearch_resident_command_uses_current_zf_cli(monkeypatch):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")

    command = build_autoresearch_resident_command(
        state_dir=Path("/tmp/state"),
        interval_seconds=2.5,
        max_actions_per_tick=3,
        worktree_root=Path("/tmp/ar-worktrees"),
        output_root=Path("/tmp/ar-output"),
        self_repair_consumer=True,
        self_repair_spawn=True,
        self_repair_backend="claude-code",
    )

    assert command == [
        "uv",
        "--project",
        "/repo",
        "run",
        "zf",
        "autoresearch",
        "resident",
        "--watch",
        "--execute",
        "--state-dir",
        "/tmp/state",
        "--interval-seconds",
        "2.5",
        "--max-actions-per-tick",
        "3",
        "--worktree-root",
        "/tmp/ar-worktrees",
        "--output-root",
        "/tmp/ar-output",
        "--self-repair-consumer",
        "--self-repair-spawn",
        "--self-repair-backend",
        "claude-code",
    ]


def test_autoresearch_resident_sidecar_starts_authorized_and_stops(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    started = {}

    def fake_popen(command, **kwargs):
        started["command"] = command
        started["cwd"] = kwargs.get("cwd")
        started["env"] = kwargs.get("env")
        return FakeProcess()

    monkeypatch.setattr(
        "zf.runtime.autoresearch_resident_sidecar.subprocess.Popen",
        fake_popen,
    )
    cfg = ZfConfig(
        runtime=RuntimeConfig(
            autoresearch_resident=RuntimeAutoresearchResidentConfig(
                enabled=True,
                interval_seconds=1.5,
                max_actions_per_tick=2,
                worktree_root=str(tmp_path / "worktrees"),
            ),
        ),
    )
    events = EventSink()

    sidecar = start_autoresearch_resident_sidecar(
        config=cfg,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is not None
    assert started["command"][:4] == ["zf", "autoresearch", "resident", "--watch"]
    assert "--execute" in started["command"]
    assert "1.5" in started["command"]
    assert "--max-actions-per-tick" in started["command"]
    assert "2" in started["command"]
    assert started["cwd"] == str(tmp_path)
    assert started["env"]["ZF_AUTORESEARCH_RESIDENT"] == "authorized"
    assert sidecar.pid_path.exists()
    assert events.events[-1].type == "autoresearch.resident_sidecar.started"

    stop_autoresearch_resident_sidecar(sidecar, event_log=events)

    assert sidecar.process.terminated is True
    assert not sidecar.pid_path.exists()
    assert events.events[-1].type == "autoresearch.resident_sidecar.stopped"
