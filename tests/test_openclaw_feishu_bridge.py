from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.config.schema import (
    IntegrationsConfig,
    OpenClawFeishuBridgeBindingConfig,
    OpenClawFeishuBridgeConfig,
    OpenClawFeishuBridgeFeishuConfig,
    OpenClawFeishuBridgeInboundConfig,
    OpenClawFeishuBridgeOpenClawConfig,
    OpenClawFeishuBridgeZaofuConfig,
    OpenClawProviderConfig,
    OpenClawRemoteBindingConfig,
    ProjectConfig,
    ProvidersConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.openclaw_feishu_bridge import (
    BRIDGE_DELIVERED,
    BRIDGE_FAILED,
    BRIDGE_SEND_REQUESTED,
    push_openclaw_feishu_bridge_once,
)
from zf.runtime.openclaw_feishu_inbound import (
    BRIDGE_INBOUND_RECEIVED,
    BRIDGE_INBOUND_REJECTED,
    BRIDGE_LOOP_SKIPPED,
    handle_openclaw_feishu_inbound_payload,
)
from zf.runtime.openclaw_feishu_inbound_watch import (
    scan_openclaw_feishu_payload_dir_once,
)
from zf.runtime.openclaw_provider import OpenClawGatewayResult
from zf.cli.main import build_parser


class _FakeOpenClawClient:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[dict[str, Any]] = []

    def send_message(
        self,
        binding: OpenClawRemoteBindingConfig,
        *,
        channel: str,
        account_id: str,
        target: str,
        message: str,
        presentation: dict[str, Any] | None = None,
        agent_id: str = "zaofu-bridge",
        idempotency_key: str = "",
    ) -> OpenClawGatewayResult:
        self.calls.append({
            "binding_id": binding.id,
            "channel": channel,
            "account_id": account_id,
            "target": target,
            "message": message,
            "presentation": presentation,
            "agent_id": agent_id,
            "idempotency_key": idempotency_key,
        })
        if not self.ok:
            return OpenClawGatewayResult(
                ok=False,
                status="failed",
                reason="send failed",
            )
        return OpenClawGatewayResult(
            ok=True,
            status="completed",
            payload={
                "ok": True,
                "result": {
                    "payload": {
                        "details": {
                            "messageId": "om_sent",
                        },
                    },
                },
            },
        )


def _state(tmp_path: Path) -> tuple[EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    return log, EventWriter(log)


def _config(*, allowed_chat_ids: list[str] | None = None) -> ZfConfig:
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
                    ),
                },
            ),
        ),
        integrations=IntegrationsConfig(
            openclaw_feishu_bridge=OpenClawFeishuBridgeConfig(
                enabled=True,
                default_binding="zaofu-main",
                bindings={
                    "zaofu-main": OpenClawFeishuBridgeBindingConfig(
                        id="zaofu-main",
                        zaofu=OpenClawFeishuBridgeZaofuConfig(
                            channel_id="ch-zaofu",
                        ),
                        openclaw=OpenClawFeishuBridgeOpenClawConfig(
                            provider_binding_id="remote",
                            account_id="default",
                            agent_id="zaofu-bridge",
                        ),
                        feishu=OpenClawFeishuBridgeFeishuConfig(
                            chat_id="oc_group",
                            target="chat:oc_group",
                        ),
                        inbound=OpenClawFeishuBridgeInboundConfig(
                            allowed_chat_ids=allowed_chat_ids or [],
                        ),
                    ),
                },
            ),
        ),
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="star",
                    trigger="workflow.invoke.requested",
                    topology="star",
                    roles=["dev"],
                ),
            ],
        ),
    )


def test_openclaw_feishu_bridge_pushes_channel_message_once(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    source = writer.emit(
        "channel.message.posted",
        actor="operator",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "同步到飞书",
        },
    )
    client = _FakeOpenClawClient()

    result = push_openclaw_feishu_bridge_once(
        event_log=event_log,
        writer=writer,
        config=_config(),
        client=client,
    )

    assert result.ok is True
    assert result.sent == 1
    assert result.failed == 0
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["binding_id"] == "remote"
    assert call["channel"] == "feishu"
    assert call["target"] == "chat:oc_group"
    assert call["agent_id"] == "zaofu-bridge"
    assert "ZaoFu:ch-zaofu" in call["message"]
    assert "同步到飞书" in call["message"]
    assert source.id in call["idempotency_key"]

    events = event_log.read_all()
    event_types = [event.type for event in events]
    assert BRIDGE_SEND_REQUESTED in event_types
    assert BRIDGE_DELIVERED in event_types
    delivered = [event for event in events if event.type == BRIDGE_DELIVERED][0]
    assert delivered.payload["source_event_id"] == source.id
    assert delivered.payload["external_message_id"] == "om_sent"

    second = push_openclaw_feishu_bridge_once(
        event_log=event_log,
        writer=writer,
        config=_config(),
        client=client,
    )

    assert second.sent == 0
    assert len(client.calls) == 1


def test_openclaw_feishu_bridge_skips_loop_and_system_messages(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    writer.emit(
        "channel.message.posted",
        payload={
            "channel_id": "ch-zaofu",
            "message_id": "msg-loop",
            "member_id": "bridge",
            "role": "assistant",
            "source": "openclaw_feishu_bridge",
            "text": "loop",
        },
    )
    writer.emit(
        "channel.message.posted",
        payload={
            "channel_id": "ch-zaofu",
            "message_id": "msg-system",
            "member_id": "system",
            "role": "system",
            "source": "kernel",
            "text": "system",
        },
    )
    client = _FakeOpenClawClient()

    result = push_openclaw_feishu_bridge_once(
        event_log=event_log,
        writer=writer,
        config=_config(),
        client=client,
    )

    assert result.sent == 0
    assert result.skipped == 2
    assert client.calls == []


def test_openclaw_feishu_bridge_records_failure(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    source = writer.emit(
        "channel.message.posted",
        payload={
            "channel_id": "ch-zaofu",
            "message_id": "msg-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "will fail",
        },
    )

    result = push_openclaw_feishu_bridge_once(
        event_log=event_log,
        writer=writer,
        config=_config(),
        client=_FakeOpenClawClient(ok=False),
    )

    assert result.ok is False
    assert result.failed == 1
    failed = [event for event in event_log.read_all() if event.type == BRIDGE_FAILED][0]
    assert failed.payload["source_event_id"] == source.id
    assert failed.payload["reason"] == "send failed"


def test_bridge_cli_registers_openclaw_feishu_push() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "bridge",
        "openclaw-feishu",
        "push",
        "--once",
        "--channel",
        "ch-zaofu",
        "--target",
        "chat:oc_group",
    ])

    assert args.command == "bridge"
    assert args.bridge_command == "openclaw-feishu"
    assert args.openclaw_feishu_command == "push"
    assert args.once is True
    assert args.channel == "ch-zaofu"


def test_openclaw_feishu_inbound_posts_channel_message(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "ok": True,
        "channel": "feishu",
        "action": "read",
        "message": {
            "messageId": "om_inbound_1",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf channel ch-zaofu 记录: 需要同步到 ZaoFu",
        },
    }

    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )

    assert result.ok is True
    assert result.received == 1
    assert result.posted == 1
    events = event_log.read_all()
    assert [event.type for event in events].count(BRIDGE_INBOUND_RECEIVED) == 1
    posted = [event for event in events if event.type == "channel.message.posted"][0]
    assert posted.payload["channel_id"] == "ch-zaofu"
    assert posted.payload["source"] == "openclaw_feishu_bridge"
    assert posted.payload["member_id"] == "feishu:ou_user"
    assert posted.payload["text"] == "需要同步到 ZaoFu"
    assert posted.payload["refs"]["openclaw"]["message_id"] == "om_inbound_1"


def test_openclaw_feishu_inbound_status_posts_agent_reply(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "message": {
            "messageId": "om_status",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf status",
        },
    }

    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )

    assert result.ok is True
    assert result.received == 1
    assert result.posted == 1
    events = event_log.read_all()
    received = [event for event in events if event.type == BRIDGE_INBOUND_RECEIVED][0]
    assert received.payload["command_kind"] == "query"
    replies = [
        event for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("source") == "feishu_agent"
    ]
    assert len(replies) == 1
    assert replies[0].payload["member_id"] == "zaofu-feishu-agent"
    assert "Tasks:" in replies[0].payload["text"]


def test_openclaw_feishu_inbound_help_lists_supported_commands(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "message": {
            "messageId": "om_help",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf help",
        },
    }

    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )

    assert result.ok is True
    events = event_log.read_all()
    received = [event for event in events if event.type == BRIDGE_INBOUND_RECEIVED][0]
    assert received.payload["command_kind"] == "help"
    replies = [
        event for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("source") == "feishu_agent"
    ]
    assert len(replies) == 1
    assert "/zf workflow invoke" in replies[0].payload["text"]
    assert "Auth-gated commands" in replies[0].payload["text"]


def test_openclaw_feishu_inbound_auth_gated_command_is_not_channel_posted(
    tmp_path: Path,
) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "message": {
            "messageId": "om_create",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf create Do not create without auth mapping",
        },
    }

    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )

    assert result.ok is True
    events = event_log.read_all()
    received = [event for event in events if event.type == BRIDGE_INBOUND_RECEIVED][0]
    assert received.payload["command_kind"] == "unsupported_auth_required"
    assert not any(event.type == "task.created" for event in events)
    replies = [
        event for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("source") == "feishu_agent"
    ]
    assert len(replies) == 1
    assert "not enabled through OpenClaw Feishu bridge yet" in replies[0].payload["text"]


def test_openclaw_feishu_bridge_replies_to_inbound_source_chat(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "message": {
            "messageId": "om_status_dm",
            "chatId": "oc_dm",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf status",
        },
    }

    inbound = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(allowed_chat_ids=["oc_dm"]),
        payload=payload,
    )
    client = _FakeOpenClawClient()
    outbound = push_openclaw_feishu_bridge_once(
        event_log=event_log,
        writer=writer,
        config=_config(allowed_chat_ids=["oc_dm"]),
        client=client,
    )

    assert inbound.ok is True
    assert outbound.ok is True
    assert outbound.sent == 1
    assert len(client.calls) == 1
    assert client.calls[0]["target"] == "chat:oc_dm"
    assert client.calls[0]["idempotency_key"].endswith(":chat:oc_dm")
    requested = [
        event for event in event_log.read_all()
        if event.type == BRIDGE_SEND_REQUESTED
    ][0]
    assert requested.payload["target"] == "chat:oc_dm"


def test_openclaw_feishu_inbound_workflow_invoke_posts_feedback(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "message": {
            "messageId": "om_workflow",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf workflow invoke TASK-1 pattern_id=star reason='approved plan'",
        },
    }

    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )

    assert result.ok is True
    events = event_log.read_all()
    workflow = [event for event in events if event.type == "workflow.invoke.requested"][0]
    assert workflow.task_id == "TASK-1"
    assert workflow.payload["pattern_id"] == "star"
    assert workflow.payload["source"] == "feishu_agent"
    assert workflow.payload["source_refs"]["feishu_message_id"] == "om_workflow"
    assert workflow.payload["source_refs"]["feishu_chat_id"] == "oc_group"
    replies = [
        event for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("source") == "feishu_agent"
    ]
    assert len(replies) == 1
    assert "workflow-invoke: requested" in replies[0].payload["text"]


def test_openclaw_feishu_inbound_attention_action_posts_feedback(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "message": {
            "messageId": "om_attention",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf attention resolve attn-bridge reason='fixed by owner'",
        },
    }

    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )

    assert result.ok is True
    events = event_log.read_all()
    attention = [
        event for event in events
        if event.type == "runtime.attention.resolved"
    ][0]
    assert attention.payload["attention_id"] == "attn-bridge"
    assert attention.payload["reason"] == "fixed by owner"
    replies = [
        event for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("source") == "feishu_agent"
    ]
    assert len(replies) == 1
    assert "attention-resolve: recorded" in replies[0].payload["text"]


def test_openclaw_feishu_inbound_deduplicates_message_id(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    payload = {
        "message": {
            "messageId": "om_duplicate",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf 记录: hello",
        },
    }

    first = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )
    second = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload=payload,
    )

    assert first.posted == 1
    assert second.skipped == 1
    events = event_log.read_all()
    assert [event.type for event in events].count("channel.message.posted") == 1
    assert [event.type for event in events].count(BRIDGE_LOOP_SKIPPED) == 1


def test_openclaw_feishu_inbound_rejects_plain_text_and_wrong_chat(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)

    plain = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload={
            "message": {
                "messageId": "om_plain",
                "chatId": "oc_group",
                "senderType": "user",
                "content": "hello without command",
            },
        },
    )
    wrong_chat = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload={
            "message": {
                "messageId": "om_wrong_chat",
                "chatId": "oc_other",
                "senderType": "user",
                "content": "/zf 记录: hello",
            },
        },
    )

    assert plain.ok is False
    assert plain.rejected == 1
    assert wrong_chat.ok is False
    assert wrong_chat.rejected == 1
    events = event_log.read_all()
    assert [event.type for event in events].count(BRIDGE_INBOUND_REJECTED) == 2
    assert [event.type for event in events].count("channel.message.posted") == 0


def test_openclaw_feishu_inbound_accepts_explicit_allowed_chat(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)
    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(allowed_chat_ids=["chat:oc_dm"]),
        payload={
            "message": {
                "messageId": "om_allowed_chat",
                "chatId": "oc_dm",
                "senderId": "ou_user",
                "senderType": "user",
                "content": "/zf status",
            },
        },
    )

    assert result.ok is True
    assert result.received == 1
    assert result.posted == 1
    events = event_log.read_all()
    received = [event for event in events if event.type == BRIDGE_INBOUND_RECEIVED][0]
    assert received.payload["chat_id"] == "oc_dm"


def test_openclaw_feishu_inbound_skips_bot_loop(tmp_path: Path) -> None:
    event_log, writer = _state(tmp_path)

    result = handle_openclaw_feishu_inbound_payload(
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
        payload={
            "message": {
                "messageId": "om_bot_loop",
                "chatId": "oc_group",
                "senderType": "bot",
                "content": "[ZaoFu:ch-zaofu] bridge echo",
            },
        },
    )

    assert result.ok is True
    assert result.skipped == 1
    events = event_log.read_all()
    assert [event.type for event in events].count(BRIDGE_LOOP_SKIPPED) == 1
    assert [event.type for event in events].count("channel.message.posted") == 0


def test_bridge_cli_registers_openclaw_feishu_inbound() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "bridge",
        "openclaw-feishu",
        "inbound",
        "--payload-file",
        "payload.json",
        "--channel",
        "ch-zaofu",
        "--target",
        "chat:oc_group",
        "--allowed-chat-id",
        "oc_dm",
    ])

    assert args.command == "bridge"
    assert args.bridge_command == "openclaw-feishu"
    assert args.openclaw_feishu_command == "inbound"
    assert args.payload_file == "payload.json"
    assert args.channel == "ch-zaofu"
    assert args.allowed_chat_id == ["oc_dm"]


def test_bridge_cli_registers_openclaw_feishu_inbound_watch_and_serve() -> None:
    parser = build_parser()
    watch = parser.parse_args([
        "bridge",
        "openclaw-feishu",
        "inbound",
        "--watch",
        "--payload-dir",
        "/tmp/openclaw-feishu-inbox",
        "--max-iterations",
        "1",
    ])
    serve = parser.parse_args([
        "bridge",
        "openclaw-feishu",
        "inbound",
        "--serve",
        "--host",
        "0.0.0.0",
        "--port",
        "8788",
        "--allow-unauthenticated",
    ])

    assert watch.openclaw_feishu_command == "inbound"
    assert watch.watch is True
    assert watch.payload_dir == "/tmp/openclaw-feishu-inbox"
    assert watch.max_iterations == 1
    assert serve.serve is True
    assert serve.host == "0.0.0.0"
    assert serve.port == 8788
    assert serve.allow_unauthenticated is True


def test_openclaw_feishu_inbound_watch_processes_and_archives_payload(
    tmp_path: Path,
) -> None:
    event_log, writer = _state(tmp_path)
    payload_dir = tmp_path / "spool"
    archive_dir = payload_dir / ".processed"
    failed_dir = payload_dir / ".failed"
    payload_dir.mkdir()
    payload_file = payload_dir / "om_watch.json"
    payload_file.write_text(
        """
        {
          "message": {
            "messageId": "om_watch",
            "chatId": "oc_group",
            "senderId": "ou_user",
            "senderType": "user",
            "content": "/zf channel ch-zaofu 记录: watch payload"
          }
        }
        """,
        encoding="utf-8",
    )

    result = scan_openclaw_feishu_payload_dir_once(
        payload_dir=payload_dir,
        archive_dir=archive_dir,
        failed_dir=failed_dir,
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
    )

    assert result.ok is True
    assert result.considered == 1
    assert result.processed == 1
    assert result.failed == 0
    assert result.received == 1
    assert result.posted == 1
    assert not payload_file.exists()
    assert (archive_dir / "om_watch.json").exists()
    events = event_log.read_all()
    assert [event.type for event in events].count(BRIDGE_INBOUND_RECEIVED) == 1
    posted = [event for event in events if event.type == "channel.message.posted"][0]
    assert posted.payload["text"] == "watch payload"


def test_openclaw_feishu_inbound_watch_moves_invalid_json_to_failed(
    tmp_path: Path,
) -> None:
    event_log, writer = _state(tmp_path)
    payload_dir = tmp_path / "spool"
    archive_dir = payload_dir / ".processed"
    failed_dir = payload_dir / ".failed"
    payload_dir.mkdir()
    broken = payload_dir / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")

    result = scan_openclaw_feishu_payload_dir_once(
        payload_dir=payload_dir,
        archive_dir=archive_dir,
        failed_dir=failed_dir,
        state_dir=tmp_path / ".zf",
        event_log=event_log,
        writer=writer,
        config=_config(),
    )

    assert result.ok is False
    assert result.considered == 1
    assert result.processed == 0
    assert result.failed == 1
    assert not broken.exists()
    assert (failed_dir / "broken.json").exists()
    assert event_log.read_all() == []
