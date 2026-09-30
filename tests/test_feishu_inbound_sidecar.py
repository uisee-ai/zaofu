from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FeishuIssueMergeConfig,
    FeishuProjectGroupConfig,
    FeishuRouteConfig,
    IntegrationsConfig,
    RuntimeConfig,
    RuntimeFeishuInboundConfig,
    ZfConfig,
)
from zf.integrations.feishu.project_group_binding import (
    FeishuProjectGroupBotBinding,
    ProjectFeishuGroupBinding,
    ProjectFeishuGroupBindingStore,
)
from zf.integrations.feishu.workspace_bridge_lease import register_provider_bridge
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
        "zf.runtime.feishu_inbound_sidecar._start_bridge_process",
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


def test_issue_merge_settings_start_issue_only_bridge(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_ISSUE_MERGE_APP_SECRET", "secret_issue")
    started = {}

    def fake_popen(command, **kwargs):
        started["command"] = command
        started["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(
        "zf.runtime.feishu_inbound_sidecar._start_bridge_process", fake_popen,
    )
    cfg = ZfConfig(
        runtime=RuntimeConfig(
            feishu_inbound=RuntimeFeishuInboundConfig(enabled=True),
        ),
        integrations=IntegrationsConfig(
            feishu_issue_merge=FeishuIssueMergeConfig(
                enabled=True, app_id="cli_issue", chat_id="oc_issue",
                operator_open_id="ou_operator",
            ),
        ),
    )
    sidecar = start_feishu_inbound_sidecar(
        config=cfg, state_dir=tmp_path / ".zf", project_root=tmp_path,
    )
    assert sidecar is not None
    assert "--issue-merge" in started["command"]
    assert started["env"]["FEISHU_APP_ID"] == "cli_issue"
    assert started["env"]["FEISHU_APP_SECRET"] == "secret_issue"
    stop_feishu_inbound_sidecar(sidecar)


def test_feishu_inbound_sidecar_starts_one_bridge_per_bot(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_RUNM", "cli_arch")
    monkeypatch.setenv("FEISHU_RUNM_SECRET", "secret_arch")
    monkeypatch.setenv("FEISHU_KANBAN", "cli_pm")
    monkeypatch.setenv("FEISHU_KANBAN_SECRET", "secret_pm")
    started = []

    def fake_popen(command, **kwargs):
        started.append({
            "command": command,
            "env": kwargs.get("env") or {},
            "cwd": kwargs.get("cwd"),
        })
        return FakeProcess(pid=4242 + len(started))

    monkeypatch.setattr(
        "zf.runtime.feishu_inbound_sidecar._start_bridge_process",
        fake_popen,
    )
    cfg = ZfConfig(
        runtime=RuntimeConfig(
            feishu_inbound=RuntimeFeishuInboundConfig(enabled=True),
        ),
        integrations=IntegrationsConfig(
            feishu_routing={
                "cli_arch:oc_group": FeishuRouteConfig(target="run_manager"),
                "cli_pm:oc_group": FeishuRouteConfig(target="kanban_agent"),
            },
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
    assert len(started) == 2
    assert [item["env"]["FEISHU_APP_ID"] for item in started] == [
        "cli_arch",
        "cli_pm",
    ]
    assert [
        event.payload["bot_purpose"]
        for event in events.events
        if event.type == "feishu.inbound_bridge.started"
    ] == ["run_manager", "kanban_agent"]

    stop_feishu_inbound_sidecar(sidecar, event_log=events)
    assert len([
        event for event in events.events
        if event.type == "feishu.inbound_bridge.stopped"
    ]) == 2


def test_feishu_inbound_sidecar_uses_provider_binding_singleton_command(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_KANBAN", "cli_pm")
    monkeypatch.setenv("FEISHU_KANBAN_SECRET", "secret_pm")
    started = {}

    def fake_popen(command, **kwargs):
        started["command"] = command
        started["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(
        "zf.runtime.feishu_inbound_sidecar._start_bridge_process",
        fake_popen,
    )
    state_dir = tmp_path / ".zf"
    binding = ProjectFeishuGroupBinding(
        binding_id="project-collaboration",
        workspace_id="team",
        project_id="project-1",
        group_kind="collaboration",
        display_name="ZaoFu · test",
        status="active",
        chat_id="oc_group",
        owner_open_id="ou_owner",
        owner_open_id_env="ZF_OWNER",
        provisioner_purpose="kanban_agent",
        primary_responder="kanban_agent",
        channel_id="zaofu",
        bots=(FeishuProjectGroupBotBinding(
            purpose="kanban_agent",
            app_id="cli_pm",
            target="kanban_agent",
            default_member="zf-product-manager",
            membership_status="active",
        ),),
        config_digest="digest",
    )
    ProjectFeishuGroupBindingStore(state_dir).upsert(binding)
    cfg = ZfConfig(
        runtime=RuntimeConfig(
            feishu_inbound=RuntimeFeishuInboundConfig(enabled=True),
        ),
        integrations=IntegrationsConfig(
            feishu_project_group=FeishuProjectGroupConfig(enabled=True),
        ),
    )

    sidecar = start_feishu_inbound_sidecar(
        config=cfg,
        state_dir=state_dir,
        project_root=tmp_path,
        event_log=EventSink(),
    )

    assert sidecar is not None
    assert "--all-workspaces" in started["command"]
    assert "--workspace" not in started["command"]
    assert started["command"][started["command"].index("--app-id") + 1] == "cli_pm"
    stop_feishu_inbound_sidecar(sidecar)


def test_feishu_inbound_sidecar_joins_existing_provider_bridge_across_workspaces(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_CLI_CMD", "zf")
    monkeypatch.setenv("FEISHU_KANBAN", "cli_pm")
    monkeypatch.setenv("FEISHU_KANBAN_SECRET", "secret_pm")
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    monkeypatch.setattr(
        "zf.integrations.feishu.workspace_bridge_lease._pid_alive",
        lambda _pid: True,
    )
    started = []
    monkeypatch.setattr(
        "zf.runtime.feishu_inbound_sidecar._start_bridge_process",
        lambda *args, **kwargs: started.append((args, kwargs)),
    )
    register_provider_bridge(
        workspace_id="team",
        app_id="cli_pm",
        project_id="project-owner",
        pid=4242,
        log_path=tmp_path / "owner.log",
    )
    state_dir = tmp_path / ".zf"
    ProjectFeishuGroupBindingStore(state_dir).upsert(ProjectFeishuGroupBinding(
        binding_id="project-collaboration",
        workspace_id="other",
        project_id="project-joiner",
        group_kind="collaboration",
        display_name="ZaoFu · test",
        status="active",
        chat_id="oc_group",
        owner_open_id="ou_owner",
        owner_open_id_env="ZF_OWNER",
        provisioner_purpose="kanban_agent",
        primary_responder="kanban_agent",
        channel_id="zaofu",
        bots=(FeishuProjectGroupBotBinding(
            purpose="kanban_agent",
            app_id="cli_pm",
            target="kanban_agent",
            default_member="zf-product-manager",
            membership_status="active",
        ),),
        config_digest="digest",
    ))
    cfg = ZfConfig(
        runtime=RuntimeConfig(
            feishu_inbound=RuntimeFeishuInboundConfig(enabled=True),
        ),
        integrations=IntegrationsConfig(
            feishu_project_group=FeishuProjectGroupConfig(enabled=True),
        ),
    )
    events = EventSink()

    sidecar = start_feishu_inbound_sidecar(
        config=cfg,
        state_dir=state_dir,
        project_root=tmp_path,
        event_log=events,
    )

    assert sidecar is not None
    assert sidecar.process is None
    assert started == []
    assert events.events[-1].type == "feishu.inbound_bridge.shared"
    stop_feishu_inbound_sidecar(sidecar, event_log=events)
    assert events.events[-1].type == "feishu.inbound_bridge.detached"
