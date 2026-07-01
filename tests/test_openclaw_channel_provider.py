from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from zf.core.config.schema import (
    OpenClawProviderConfig,
    OpenClawRemoteBindingConfig,
    ProjectConfig,
    ProvidersConfig,
    ZfConfig,
)
from zf.core.workspace.providers import WorkspaceProviderRegistry
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import route_channel_message
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.openclaw_provider import OpenClawGatewayResult


class _FakeOpenClawClient:
    def __init__(self, *, preflight_ok: bool = True, reply_ok: bool = True) -> None:
        self.preflight_ok = preflight_ok
        self.reply_ok = reply_ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def preflight(self, binding: OpenClawRemoteBindingConfig) -> OpenClawGatewayResult:
        self.calls.append(("preflight", {"binding_id": binding.id}))
        if not self.preflight_ok:
            return OpenClawGatewayResult(
                ok=False,
                status="missing_token",
                reason="OPENCLAW_GATEWAY_TOKEN is not set",
            )
        return OpenClawGatewayResult(ok=True, status="completed")

    def ensure_agent(
        self,
        binding: OpenClawRemoteBindingConfig,
        descriptor: dict[str, Any],
    ) -> OpenClawGatewayResult:
        self.calls.append(("ensure_agent", descriptor))
        return OpenClawGatewayResult(
            ok=True,
            status="skipped",
            provider_session_id=f"openclaw:{binding.id}:{descriptor['id']}",
        )

    def run_turn(
        self,
        binding: OpenClawRemoteBindingConfig,
        *,
        agent_id: str,
        prompt: str,
        system_prompt: str,
        timeout_seconds: float,
        metadata: dict[str, Any],
    ) -> OpenClawGatewayResult:
        self.calls.append((
            "run_turn",
            {
                "binding_id": binding.id,
                "agent_id": agent_id,
                "prompt": prompt,
                "system_prompt": system_prompt,
                "metadata": metadata,
                "timeout_seconds": timeout_seconds,
            },
        ))
        if not self.reply_ok:
            return OpenClawGatewayResult(
                ok=False,
                status="timeout",
                reason="gateway timeout",
            )
        return OpenClawGatewayResult(
            ok=True,
            status="completed",
            provider_session_id=f"openclaw:{binding.id}:{agent_id}",
            reply="openclaw channel reply",
            usage={"total_tokens": 17},
        )


@pytest.fixture(autouse=True)
def _isolated_workspace_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return state_dir


def _writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(state_dir / "events.jsonl"))


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="demo", state_dir=".zf"),
        providers=ProvidersConfig(
            openclaw=OpenClawProviderConfig(
                default_binding="remote",
                bindings={
                    "remote": OpenClawRemoteBindingConfig(
                        id="remote",
                        base_url="http://openclaw.example",
                        token_env="OPENCLAW_GATEWAY_TOKEN",
                        timeout_seconds=2.0,
                    ),
                },
            ),
        ),
    )


def _remote_config(base_url: str, token_env: str) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="demo", state_dir=".zf"),
        providers=ProvidersConfig(
            openclaw=OpenClawProviderConfig(
                default_binding="remote",
                bindings={
                    "remote": OpenClawRemoteBindingConfig(
                        id="remote",
                        base_url=base_url,
                        token_env=token_env,
                        timeout_seconds=float(
                            os.environ.get("ZF_OPENCLAW_REMOTE_TIMEOUT_S", "20")
                        ),
                    ),
                },
            ),
        ),
    )


def test_openclaw_channel_member_connects_through_controlled_action(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-invite-member", "request": {}},
    )
    client = _FakeOpenClawClient()
    service = ControlledActionService(
        state_dir,
        writer,
        config=_config(),
        actor="web",
        source="channel",
        surface="web",
        openclaw_client=client,
    )

    response = service.execute(
        action="channel-invite-member",
        requested_action="channel.add_member",
        requested=requested,
        payload={
            "channel_id": "ch-demo",
            "member_id": "openclaw-reviewer",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": "remote",
            "channel_role": "dev_reviewer",
            "permissions": ["read", "message", "summarize"],
        },
    )

    detail = project_channel(state_dir, "ch-demo")
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]

    assert response["status"] == "connected"
    assert response["provider_binding_id"] == "remote"
    assert ("preflight", {"binding_id": "remote"}) in client.calls
    assert "channel.member.invited" in event_types
    assert "channel.member.connected" in event_types
    assert "provider.health.changed" in event_types
    assert detail is not None
    member = detail["members"][0]
    assert member["status"] == "connected"
    assert member["provider"] == "openclaw"
    assert member["provider_binding_id"] == "remote"
    assert member["remote_agent_id"].startswith("zaofu_demo_ch_demo")


def test_openclaw_channel_member_uses_workspace_provider_registry(tmp_path: Path) -> None:
    WorkspaceProviderRegistry().upsert_openclaw_binding(
        OpenClawRemoteBindingConfig(
            id="remote",
            base_url="http://openclaw.example",
            token_env="OPENCLAW_GATEWAY_TOKEN",
            timeout_seconds=2.0,
        ),
        default=True,
    )
    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-invite-member", "request": {}},
    )
    client = _FakeOpenClawClient()
    service = ControlledActionService(
        state_dir,
        writer,
        config=ZfConfig(project=ProjectConfig(name="demo", state_dir=".zf")),
        actor="web",
        source="channel",
        surface="web",
        openclaw_client=client,
    )

    response = service.execute(
        action="channel-invite-member",
        requested_action="channel.add_member",
        requested=requested,
        payload={
            "channel_id": "ch-demo",
            "member_id": "openclaw-reviewer",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": "remote",
            "channel_role": "dev_reviewer",
            "permissions": ["read", "message", "summarize"],
        },
    )

    assert response["status"] == "connected"
    assert response["provider_binding_id"] == "remote"
    assert ("preflight", {"binding_id": "remote"}) in client.calls


def test_openclaw_channel_member_rejects_missing_binding(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-invite-member", "request": {}},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=ZfConfig(project=ProjectConfig(name="demo", state_dir=".zf")),
        actor="web",
        source="channel",
        surface="web",
        openclaw_client=_FakeOpenClawClient(),
    )

    response = service.execute(
        action="channel-invite-member",
        requested_action="channel.add_member",
        requested=requested,
        payload={
            "channel_id": "ch-demo",
            "member_id": "openclaw-reviewer",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "provider_binding_id": "remote",
            "permissions": ["read", "message"],
        },
    )

    detail = project_channel(state_dir, "ch-demo")
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]

    assert response["status"] == "rejected"
    assert response["_status_code"] == 409
    assert "channel.member.add.rejected" in event_types
    assert "channel.member.invited" not in event_types
    assert detail is not None
    assert detail["members"][0]["status"] == "rejected"


def test_openclaw_channel_member_rejects_missing_token_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    WorkspaceProviderRegistry().upsert_openclaw_binding(
        OpenClawRemoteBindingConfig(
            id="remote",
            base_url="http://openclaw.example",
            token_env="OPENCLAW_GATEWAY_TOKEN",
            timeout_seconds=2.0,
        ),
        default=True,
    )
    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-invite-member", "request": {}},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=ZfConfig(project=ProjectConfig(name="demo", state_dir=".zf")),
        actor="web",
        source="channel",
        surface="web",
    )

    response = service.execute(
        action="channel-invite-member",
        requested_action="channel.add_member",
        requested=requested,
        payload={
            "channel_id": "ch-demo",
            "member_id": "openclaw-reviewer",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": "remote",
            "permissions": ["read", "message"],
        },
    )

    detail = project_channel(state_dir, "ch-demo")
    events = EventLog(state_dir / "events.jsonl").read_all()
    health_events = [
        event for event in events if event.type == "provider.health.changed"
    ]

    assert response["status"] == "rejected"
    assert response["_status_code"] == 409
    assert "OPENCLAW_GATEWAY_TOKEN is not set" in response["reason"]
    assert detail is not None
    assert detail["members"][0]["status"] == "rejected"
    assert health_events
    assert health_events[-1].payload["backend"] == "openclaw"
    assert health_events[-1].payload["status"] == "blocked"
    assert health_events[-1].payload["binding_id"] == "remote"


def test_openclaw_mention_dispatches_remote_reply(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    client = _FakeOpenClawClient()
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-demo",
        payload={
            "channel_id": "ch-demo",
            "thread_id": "main",
            "member_id": "openclaw-reviewer",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": "remote",
            "remote_agent_id": "zaofu_demo_ch_demo_openclaw_reviewer",
            "provider_session_id": "openclaw:remote:zaofu_demo_ch_demo_openclaw_reviewer",
            "channel_role": "dev_reviewer",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    writer.emit(
        "channel.member.connected",
        actor="web",
        correlation_id="ch-demo",
        payload={
            "channel_id": "ch-demo",
            "thread_id": "main",
            "member_id": "openclaw-reviewer",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": "remote",
            "remote_agent_id": "zaofu_demo_ch_demo_openclaw_reviewer",
            "provider_session_id": "openclaw:remote:zaofu_demo_ch_demo_openclaw_reviewer",
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-demo",
        payload={
            "channel_id": "ch-demo",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@openclaw review this design",
            "mentions": ["openclaw-reviewer"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        config=_config(),
        openclaw_client=client,
    )
    detail = project_channel(state_dir, "ch-demo")

    assert result.targets == ["openclaw-reviewer"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "completed"
    assert detail["reply_requests"][0]["provider_binding_id"] == "remote"
    assert any(item["text"] == "openclaw channel reply" for item in detail["messages"])
    run_call = [call for call in client.calls if call[0] == "run_turn"][0][1]
    assert run_call["binding_id"] == "remote"
    assert "@openclaw review this design" in run_call["prompt"]


def test_openclaw_remote_smoke_opt_in(tmp_path: Path) -> None:
    if os.environ.get("ZF_OPENCLAW_REMOTE_SMOKE") != "1":
        pytest.skip("set ZF_OPENCLAW_REMOTE_SMOKE=1 to run real OpenClaw smoke")
    base_url = os.environ.get("ZF_OPENCLAW_REMOTE_URL", "").strip()
    token_env = os.environ.get(
        "ZF_OPENCLAW_REMOTE_TOKEN_ENV",
        "OPENCLAW_GATEWAY_TOKEN",
    ).strip()
    if not base_url:
        pytest.skip("set ZF_OPENCLAW_REMOTE_URL to run real OpenClaw smoke")
    if token_env and not os.environ.get(token_env):
        pytest.skip(f"set {token_env} to run real OpenClaw smoke")

    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    config = _remote_config(base_url, token_env)
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-invite-member", "request": {}},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        actor="web",
        source="channel",
        surface="web",
    )

    response = service.execute(
        action="channel-invite-member",
        requested_action="channel.add_member",
        requested=requested,
        payload={
            "channel_id": "ch-openclaw-smoke",
            "member_id": "openclaw-smoke",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": "remote",
            "channel_role": "dev_reviewer",
            "permissions": ["read", "message", "summarize"],
        },
    )
    assert response["status"] == "connected"

    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-openclaw-smoke",
        payload={
            "channel_id": "ch-openclaw-smoke",
            "thread_id": "main",
            "message_id": "msg-openclaw-smoke",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@openclaw-smoke respond with one short sentence",
            "mentions": ["openclaw-smoke"],
        },
    )
    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        config=config,
    )
    detail = project_channel(state_dir, "ch-openclaw-smoke")

    assert result.targets == ["openclaw-smoke"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "completed"
    assert any(item["role"] == "assistant" for item in detail["messages"])


def test_openclaw_workspace_remote_smoke_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("ZF_OPENCLAW_WORKSPACE_SMOKE") != "1":
        pytest.skip("set ZF_OPENCLAW_WORKSPACE_SMOKE=1 to run workspace OpenClaw smoke")
    monkeypatch.delenv("ZF_WORKSPACE_HOME", raising=False)
    workspace = os.environ.get("ZF_WORKSPACE", "default")
    try:
        openclaw = WorkspaceProviderRegistry(workspace=workspace).openclaw()
    except ValueError as exc:
        pytest.skip(str(exc))
    binding_id = str(openclaw.default_binding or "")
    if not binding_id and len(openclaw.bindings) == 1:
        binding_id = next(iter(openclaw.bindings))
    if not binding_id:
        pytest.skip("workspace OpenClaw default binding is not configured")
    binding = openclaw.bindings.get(binding_id)
    if binding is None:
        pytest.skip(f"workspace OpenClaw binding {binding_id!r} is not configured")
    if binding.token_env and not os.environ.get(binding.token_env):
        pytest.skip(f"set {binding.token_env} to run workspace OpenClaw smoke")

    state_dir = _state(tmp_path)
    writer = _writer(state_dir)
    config = ZfConfig(project=ProjectConfig(name="demo", state_dir=".zf"))
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-invite-member", "request": {}},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        actor="web",
        source="channel",
        surface="web",
    )

    response = service.execute(
        action="channel-invite-member",
        requested_action="channel.add_member",
        requested=requested,
        payload={
            "channel_id": "ch-openclaw-workspace-smoke",
            "member_id": "openclaw-workspace-smoke",
            "member_type": "provider_agent",
            "provider": "openclaw",
            "backend": "openclaw",
            "provider_binding_id": binding_id,
            "channel_role": "dev_reviewer",
            "permissions": ["read", "message", "summarize"],
        },
    )
    assert response["status"] == "connected"

    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-openclaw-workspace-smoke",
        payload={
            "channel_id": "ch-openclaw-workspace-smoke",
            "thread_id": "main",
            "message_id": "msg-openclaw-workspace-smoke",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@openclaw-workspace-smoke respond with one short sentence",
            "mentions": ["openclaw-workspace-smoke"],
        },
    )
    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        config=config,
    )
    detail = project_channel(state_dir, "ch-openclaw-workspace-smoke")

    assert result.targets == ["openclaw-workspace-smoke"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "completed"
    assert any(item["role"] == "assistant" for item in detail["messages"])
