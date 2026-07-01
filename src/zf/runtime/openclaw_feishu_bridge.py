"""OpenClaw-backed Feishu bridge for ZaoFu channel events."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from zf.core.config.schema import (
    OpenClawFeishuBridgeBindingConfig,
    OpenClawFeishuBridgeConfig,
    OpenClawFeishuBridgeFeishuConfig,
    OpenClawFeishuBridgeInboundConfig,
    OpenClawFeishuBridgeOpenClawConfig,
    OpenClawFeishuBridgeZaofuConfig,
    ZfConfig,
)
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.core.security.redaction import redact_obj
from zf.runtime.openclaw_provider import (
    OpenClawGatewayClient,
    OpenClawGatewayResult,
    resolve_openclaw_binding,
)

BRIDGE_SOURCE = "openclaw_feishu_bridge"
BRIDGE_DELIVERED = "bridge.message.delivered"
BRIDGE_FAILED = "bridge.message.failed"
BRIDGE_SEND_REQUESTED = "bridge.message.send.requested"


@dataclass(frozen=True)
class OpenClawFeishuBridgePushResult:
    ok: bool
    status: str
    reason: str = ""
    considered: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    delivered_event_ids: list[str] | None = None
    failed_event_ids: list[str] | None = None


def push_openclaw_feishu_bridge_once(
    *,
    event_log: EventLog,
    writer: EventWriter,
    config: ZfConfig | None,
    bridge_binding_id: str = "",
    channel_id: str = "",
    target: str = "",
    provider_binding_id: str = "",
    client: OpenClawGatewayClient | None = None,
) -> OpenClawFeishuBridgePushResult:
    bridge_binding, binding_error = resolve_openclaw_feishu_bridge_binding(
        config,
        bridge_binding_id=bridge_binding_id,
        channel_id=channel_id,
        target=target,
        provider_binding_id=provider_binding_id,
    )
    if binding_error:
        return OpenClawFeishuBridgePushResult(ok=False, status="config_error", reason=binding_error)

    provider_id = (
        provider_binding_id
        or bridge_binding.openclaw.provider_binding_id
        or ""
    )
    openclaw_binding, openclaw_error = resolve_openclaw_binding(config, provider_id)
    if openclaw_error or openclaw_binding is None:
        return OpenClawFeishuBridgePushResult(
            ok=False,
            status="provider_error",
            reason=openclaw_error,
        )

    events = event_log.read_all()
    delivered_source_ids = _delivered_source_ids(events, bridge_id=bridge_binding.id)
    selected = [
        event for event in events
        if _should_send_event(
            event,
            bridge_binding=bridge_binding,
            delivered_source_ids=delivered_source_ids,
        )
    ]

    gateway = client or OpenClawGatewayClient()
    delivered_event_ids: list[str] = []
    failed_event_ids: list[str] = []
    for event in selected:
        payload = event.payload if isinstance(event.payload, dict) else {}
        message = _format_feishu_message(event, bridge_binding=bridge_binding)
        feishu_target = _event_feishu_target(event, bridge_binding=bridge_binding)
        requested = writer.emit(
            BRIDGE_SEND_REQUESTED,
            actor="zf-bridge",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id or _payload_str(payload, "channel_id"),
            payload=redact_obj(_base_bridge_payload(
                event,
                bridge_binding,
                openclaw_binding.id,
                feishu_target=feishu_target,
            )),
        )
        result = gateway.send_message(
            openclaw_binding,
            channel="feishu",
            account_id=bridge_binding.openclaw.account_id,
            target=feishu_target,
            message=message,
            agent_id=bridge_binding.openclaw.agent_id,
            idempotency_key=_outbound_idempotency_key(
                event,
                bridge_binding,
                feishu_target=feishu_target,
            ),
        )
        if result.ok:
            delivered = writer.emit(
                BRIDGE_DELIVERED,
                actor="zf-bridge",
                task_id=event.task_id,
                causation_id=requested.id,
                correlation_id=event.correlation_id or _payload_str(payload, "channel_id"),
                payload=redact_obj({
                    **_base_bridge_payload(
                        event,
                        bridge_binding,
                        openclaw_binding.id,
                        feishu_target=feishu_target,
                    ),
                    "external_message_id": _external_message_id(result),
                    "status": result.status,
                    "result": result.payload,
                }),
            )
            delivered_event_ids.append(delivered.id)
            continue
        failed = writer.emit(
            BRIDGE_FAILED,
            actor="zf-bridge",
            task_id=event.task_id,
            causation_id=requested.id,
            correlation_id=event.correlation_id or _payload_str(payload, "channel_id"),
            payload=redact_obj({
                **_base_bridge_payload(
                    event,
                    bridge_binding,
                    openclaw_binding.id,
                    feishu_target=feishu_target,
                ),
                "status": result.status,
                "reason": result.reason,
                "result": result.payload,
            }),
        )
        failed_event_ids.append(failed.id)

    considered = sum(
        1
        for event in events
        if event.type in set(bridge_binding.outbound.include_event_types)
        and _payload_str(event.payload, "channel_id") == bridge_binding.zaofu.channel_id
    )
    failed_count = len(failed_event_ids)
    return OpenClawFeishuBridgePushResult(
        ok=failed_count == 0,
        status="completed" if failed_count == 0 else "failed",
        considered=considered,
        sent=len(delivered_event_ids),
        skipped=max(considered - len(selected), 0),
        failed=failed_count,
        delivered_event_ids=delivered_event_ids,
        failed_event_ids=failed_event_ids,
    )


def resolve_openclaw_feishu_bridge_binding(
    config: ZfConfig | None,
    *,
    bridge_binding_id: str = "",
    channel_id: str = "",
    target: str = "",
    provider_binding_id: str = "",
    allowed_chat_ids: list[str] | None = None,
    require_outbound: bool = True,
) -> tuple[OpenClawFeishuBridgeBindingConfig, str]:
    bridge = _bridge_config(config)
    selected: OpenClawFeishuBridgeBindingConfig | None = None
    if bridge.enabled and bridge.bindings:
        selected_id = bridge_binding_id or bridge.default_binding
        if not selected_id and len(bridge.bindings) == 1:
            selected_id = next(iter(bridge.bindings))
        if selected_id:
            selected = bridge.bindings.get(selected_id)
            if selected is None:
                return _empty_binding(), f"openclaw feishu bridge binding {selected_id!r} is not configured"
    if selected is None and not (channel_id and target):
        return _empty_binding(), "openclaw feishu bridge is not configured; provide zf.yaml integration or --channel/--target"
    binding = selected or _empty_binding()
    if channel_id:
        binding = replace(
            binding,
            zaofu=replace(binding.zaofu, channel_id=channel_id),
        )
    if target:
        binding = replace(
            binding,
            feishu=replace(binding.feishu, target=target),
        )
    if provider_binding_id:
        binding = replace(
            binding,
            openclaw=replace(binding.openclaw, provider_binding_id=provider_binding_id),
        )
    allowed_chat_ids = allowed_chat_ids or []
    if allowed_chat_ids:
        binding = replace(
            binding,
            inbound=replace(
                binding.inbound,
                allowed_chat_ids=[
                    *binding.inbound.allowed_chat_ids,
                    *allowed_chat_ids,
                ],
            ),
        )
    if not binding.zaofu.channel_id:
        return binding, "openclaw feishu bridge zaofu.channel_id is required"
    if not binding.feishu.target:
        return binding, "openclaw feishu bridge feishu.target is required"
    if require_outbound and not binding.outbound.enabled:
        return binding, "openclaw feishu bridge outbound is disabled"
    return binding, ""


def bridge_status(event_log: EventLog, *, bridge_id: str = "") -> dict[str, int]:
    events = event_log.read_all()
    return {
        "send_requested": _count_bridge_events(events, BRIDGE_SEND_REQUESTED, bridge_id),
        "delivered": _count_bridge_events(events, BRIDGE_DELIVERED, bridge_id),
        "failed": _count_bridge_events(events, BRIDGE_FAILED, bridge_id),
        "inbound_received": _count_bridge_events(events, "bridge.inbound.received", bridge_id),
        "inbound_rejected": _count_bridge_events(events, "bridge.inbound.rejected", bridge_id),
        "loop_skipped": _count_bridge_events(events, "bridge.loop.skipped", bridge_id),
    }


def _bridge_config(config: ZfConfig | None) -> OpenClawFeishuBridgeConfig:
    if config is None:
        return OpenClawFeishuBridgeConfig()
    integrations = getattr(config, "integrations", None)
    return getattr(integrations, "openclaw_feishu_bridge", OpenClawFeishuBridgeConfig())


def _empty_binding() -> OpenClawFeishuBridgeBindingConfig:
    return OpenClawFeishuBridgeBindingConfig(
        id="cli",
        zaofu=OpenClawFeishuBridgeZaofuConfig(),
        openclaw=OpenClawFeishuBridgeOpenClawConfig(),
        feishu=OpenClawFeishuBridgeFeishuConfig(),
        inbound=OpenClawFeishuBridgeInboundConfig(),
    )


def _should_send_event(
    event: ZfEvent,
    *,
    bridge_binding: OpenClawFeishuBridgeBindingConfig,
    delivered_source_ids: set[str],
) -> bool:
    if event.id in delivered_source_ids:
        return False
    if event.type not in set(bridge_binding.outbound.include_event_types):
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    if _payload_str(payload, "channel_id") != bridge_binding.zaofu.channel_id:
        return False
    if _payload_str(payload, "source") == BRIDGE_SOURCE:
        return False
    role = _payload_str(payload, "role")
    if role and role in set(bridge_binding.outbound.exclude_roles):
        return False
    return bool(_payload_str(payload, "text") or _payload_str(payload, "message"))


def _delivered_source_ids(events: list[ZfEvent], *, bridge_id: str) -> set[str]:
    source_ids: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type != BRIDGE_DELIVERED:
            continue
        if bridge_id and _payload_str(payload, "bridge_id") != bridge_id:
            continue
        source_event_id = _payload_str(payload, "source_event_id")
        if source_event_id:
            source_ids.add(source_event_id)
    return source_ids


def _format_feishu_message(
    event: ZfEvent,
    *,
    bridge_binding: OpenClawFeishuBridgeBindingConfig,
) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = _payload_str(payload, "channel_id") or bridge_binding.zaofu.channel_id
    member_id = _payload_str(payload, "member_id") or event.actor or "zaofu"
    role = _payload_str(payload, "role") or "message"
    text = _payload_str(payload, "text") or _payload_str(payload, "message")
    return f"[ZaoFu:{channel_id}] {member_id} ({role})\n{text}".strip()


def _base_bridge_payload(
    event: ZfEvent,
    bridge_binding: OpenClawFeishuBridgeBindingConfig,
    provider_binding_id: str,
    *,
    feishu_target: str | None = None,
) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    target = feishu_target or bridge_binding.feishu.target
    return {
        "bridge_id": bridge_binding.id,
        "source": BRIDGE_SOURCE,
        "source_event_id": event.id,
        "source_event_type": event.type,
        "channel_id": _payload_str(payload, "channel_id") or bridge_binding.zaofu.channel_id,
        "thread_id": _payload_str(payload, "thread_id") or bridge_binding.zaofu.thread_id,
        "message_id": _payload_str(payload, "message_id"),
        "provider": "openclaw",
        "provider_binding_id": provider_binding_id,
        "openclaw_channel": "feishu",
        "account_id": bridge_binding.openclaw.account_id,
        "target": target,
        "idempotency_key": _outbound_idempotency_key(
            event,
            bridge_binding,
            feishu_target=target,
        ),
    }


def _outbound_idempotency_key(
    event: ZfEvent,
    bridge_binding: OpenClawFeishuBridgeBindingConfig,
    *,
    feishu_target: str | None = None,
) -> str:
    target = feishu_target or bridge_binding.feishu.target
    return f"zaofu:{event.id}:openclaw:feishu:{target}"


def _event_feishu_target(
    event: ZfEvent,
    *,
    bridge_binding: OpenClawFeishuBridgeBindingConfig,
) -> str:
    if not bridge_binding.outbound.reply_to_inbound_source:
        return bridge_binding.feishu.target
    payload = event.payload if isinstance(event.payload, dict) else {}
    refs = payload.get("refs")
    refs = refs if isinstance(refs, dict) else {}
    # feishu-C #2: an explicit standardized origin chat (refs.openclaw /
    # refs.feishu — both namespaces) routes the reply back to that chat
    # regardless of the reply's backend source. This is what lets a channel
    # agent reply (source=backend) return to the originating Feishu chat once
    # the origin ref is propagated onto the reply.
    origin = (
        _payload_str(refs.get("openclaw"), "chat_id")
        or _payload_str(refs.get("feishu"), "chat_id")
    )
    if origin:
        return f"chat:{_target_chat_id(origin)}"
    # legacy source-gated path (back-compat: source=feishu_agent + refs.chat_id).
    if _payload_str(payload, "source") in {"feishu_agent", "feishu"}:
        legacy = _payload_str(refs, "chat_id")
        if legacy:
            return f"chat:{_target_chat_id(legacy)}"
    return bridge_binding.feishu.target


def _target_chat_id(target: str) -> str:
    raw = str(target or "").strip()
    if raw.startswith("chat:"):
        return raw.split(":", 1)[1].strip()
    return raw


def _external_message_id(result: OpenClawGatewayResult) -> str:
    return _first_nested_text(
        result.payload,
        keys=("messageId", "message_id", "external_message_id"),
    )


def _first_nested_text(value: Any, *, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _first_nested_text(item, keys=keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_nested_text(item, keys=keys)
            if found:
                return found
    return ""


def _payload_str(payload: Any, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _count_bridge_events(events: list[ZfEvent], event_type: str, bridge_id: str) -> int:
    count = 0
    for event in events:
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if bridge_id and _payload_str(payload, "bridge_id") != bridge_id:
            continue
        count += 1
    return count
