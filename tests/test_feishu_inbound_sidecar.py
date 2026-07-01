from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FeishuRouteConfig,
    IntegrationsConfig,
    RuntimeConfig,
    RuntimeFeishuInboundConfig,
    ZfConfig,
)
from zf.runtime.feishu_inbound_sidecar import (
    build_feishu_inbound_command,
    start_feishu_inbound_sidecar,
    stop_feishu_inbound_sidecar,
)


class EventSink:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


class FakeProcess:
    def __init__(self, pid: int = 4242) -> None:
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


def test_build_feishu_inbound_command_uses_current_zf_cli(monkeypatch):
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")

    command = build_feishu_inbound_command(
        debounce_ms=250,
        state_dir=Path("/tmp/state"),
    )

    assert command == [
        "uv",
        "--project",
        "/repo",
        "run",
        "zf",
        "feishu",
        "bridge",
        "--watch",
        "--debounce-ms",
        "250",
        "--state-dir",
        "/tmp/state",
    ]


def test_feishu_inbound_sidecar_skips_without_routing(tmp_path: Path):
    cfg = ZfConfig(
        runtime=RuntimeConfig(
            feishu_inbound=RuntimeFeishuInboundConfig(enabled=True),
        )
    )
    events = EventSink()

    sidecar = start_feishu_inbound_sidecar(
        config=cfg,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is None
    assert events.events[-1].type == "feishu.inbound_bridge.skipped"
    assert events.events[-1].payload["reason"] == "missing_feishu_routing"


def test_feishu_inbound_sidecar_starts_and_stops(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    started = {}

    def fake_popen(command, **kwargs):
        started["command"] = command
        started["cwd"] = kwargs.get("cwd")
        return FakeProcess()

    monkeypatch.setattr(
        "zf.runtime.feishu_inbound_sidecar.subprocess.Popen",
        fake_popen,
    )
    cfg = ZfConfig(
        runtime=RuntimeConfig(
            feishu_inbound=RuntimeFeishuInboundConfig(
                enabled=True,
                debounce_ms=333,
            ),
        ),
        integrations=IntegrationsConfig(
            feishu_routing={"*": FeishuRouteConfig(target="kanban_agent")},
        ),
    )
    events = EventSink()

    sidecar = start_feishu_inbound_sidecar(
        config=cfg,
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is not None
    assert started["command"][:3] == ["zf", "feishu", "bridge"]
    assert "--watch" in started["command"]
    assert "333" in started["command"]
    assert started["cwd"] == str(tmp_path)
    assert sidecar.pid_path.exists()
    assert events.events[-1].type == "feishu.inbound_bridge.started"

    stop_feishu_inbound_sidecar(sidecar, event_log=events)

    assert sidecar.process.terminated is True
    assert not sidecar.pid_path.exists()
    assert events.events[-1].type == "feishu.inbound_bridge.stopped"
