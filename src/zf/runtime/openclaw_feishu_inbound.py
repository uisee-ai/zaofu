"""Inbound OpenClaw/Feishu bridge into ZaoFu channel actions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.schema import OpenClawFeishuBridgeBindingConfig, ZfConfig
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.core.security.redaction import redact_obj
from zf.runtime.feishu_agent import (
    execute_feishu_agent_command,
    parse_feishu_agent_command,
)
from zf.runtime.openclaw_feishu_bridge import (
    BRIDGE_SOURCE,
    resolve_openclaw_feishu_bridge_binding,
)

BRIDGE_INBOUND_RECEIVED = "bridge.inbound.received"
BRIDGE_INBOUND_REJECTED = "bridge.inbound.rejected"
BRIDGE_LOOP_SKIPPED = "bridge.loop.skipped"


@dataclass(frozen=True)
class OpenClawFeishuInboundResult:
    ok: bool
    status: str
    reason: str = ""
    received: int = 0
    posted: int = 0
    rejected: int = 0
    skipped: int = 0
    event_id: str = ""
    message_event_id: str = ""
    action_response: dict[str, Any] | None = None


def handle_openclaw_feishu_inbound_payload(
    *,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
    config: ZfConfig | None,
    payload: dict[str, Any],
    bridge_binding_id: str = "",
    channel_id: str = "",
    target: str = "",
    provider_binding_id: str = "",
    allowed_chat_ids: list[str] | None = None,
    project_root: Path | None = None,
) -> OpenClawFeishuInboundResult:
    binding, binding_error = resolve_openclaw_feishu_bridge_binding(
        config,
        bridge_binding_id=bridge_binding_id,
        channel_id=channel_id,
        target=target,
        provider_binding_id=provider_binding_id,
        allowed_chat_ids=allowed_chat_ids,
        require_outbound=False,
    )
    if binding_error:
        return OpenClawFeishuInboundResult(ok=False, status="config_error", reason=binding_error)

    inbound = normalize_openclaw_feishu_payload(payload, binding=binding)
    if not inbound["message_id"]:
        rejected = _emit_rejected(
            writer,
            binding=binding,
            inbound=inbound,
            reason="message_id is required",
        )
        return OpenClawFeishuInboundResult(
            ok=False,
            status="rejected",
            reason="message_id is required",
            rejected=1,
            event_id=rejected.id,
        )

    allowed_chat_ids = _binding_allowed_chat_ids(binding)
    if (
        allowed_chat_ids
        and inbound["chat_id"]
        and inbound["chat_id"] not in allowed_chat_ids
    ):
        rejected = _emit_rejected(
            writer,
            binding=binding,
            inbound=inbound,
            reason="chat_id does not match bridge binding",
        )
        return OpenClawFeishuInboundResult(
            ok=False,
            status="rejected",
            reason="chat_id does not match bridge binding",
            rejected=1,
            event_id=rejected.id,
        )

    if _is_loop_message(inbound):
        skipped = _emit_loop_skipped(
            writer,
            binding=binding,
            inbound=inbound,
            reason="bridge or bot-originated message skipped",
        )
        return OpenClawFeishuInboundResult(
            ok=True,
            status="skipped",
            reason="bridge or bot-originated message skipped",
            skipped=1,
            event_id=skipped.id,
        )

    if _has_seen_inbound(event_log.read_all(), inbound["idempotency_key"], binding.id):
        skipped = _emit_loop_skipped(
            writer,
            binding=binding,
            inbound=inbound,
            reason="duplicate inbound message skipped",
        )
        return OpenClawFeishuInboundResult(
            ok=True,
            status="skipped",
            reason="duplicate inbound message skipped",
            skipped=1,
            event_id=skipped.id,
        )

    command = parse_inbound_command(inbound["text"], binding=binding)
    if not command.ok:
        rejected = _emit_rejected(
            writer,
            binding=binding,
            inbound=inbound,
            reason=command.reason,
        )
        return OpenClawFeishuInboundResult(
            ok=False,
            status=command.status,
            reason=command.reason,
            rejected=1,
            event_id=rejected.id,
        )

    received = writer.emit(
        BRIDGE_INBOUND_RECEIVED,
        actor="zf-bridge",
        correlation_id=command.channel_id,
        payload=redact_obj({
            **_base_inbound_payload(binding, inbound),
            "target_channel_id": command.channel_id,
            "target_thread_id": command.thread_id,
            "command_status": command.status,
            "command_kind": command.kind,
            "command": command.command,
        }),
    )
    response = execute_feishu_agent_command(
        command=command,
        state_dir=Path(state_dir),
        writer=writer,
        requested=received,
        config=config,
        project_root=project_root,
        actor="zf-bridge",
        source=BRIDGE_SOURCE,
        member_id=_member_id(inbound),
        inbound_refs=_inbound_refs(binding, inbound, received),
    )
    return OpenClawFeishuInboundResult(
        ok=bool(response.ok),
        status=response.status,
        reason=response.reason,
        received=1,
        posted=response.posted,
        event_id=received.id,
        message_event_id=response.reply_event_id,
        action_response=response.action_response,
    )


def normalize_openclaw_feishu_payload(
    payload: dict[str, Any],
    *,
    binding: OpenClawFeishuBridgeBindingConfig,
) -> dict[str, Any]:
    message = _message_payload(payload)
    account_id = _first_text(payload, "account_id", "accountId", "account") or binding.openclaw.account_id
    chat_id = (
        _first_text(message, "chat_id", "chatId", "chat")
        or _first_text(payload, "chat_id", "chatId")
        or _target_chat_id(binding.feishu.target)
        or binding.feishu.chat_id
    )
    message_id = _first_text(message, "message_id", "messageId", "id") or _first_text(
        payload,
        "message_id",
        "messageId",
    )
    text = _coerce_message_text(
        _first_text(message, "text", "content", "message")
        or _first_text(payload, "text", "content")
    )
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    sender_id = (
        _first_text(message, "sender_id", "senderId", "senderOpenId")
        or _first_text(sender, "open_id", "openId", "user_id", "userId", "id")
        or _first_text(payload, "sender_id", "senderId")
    )
    sender_name = (
        _first_text(message, "sender_name", "senderName")
        or _first_text(sender, "name", "display_name", "displayName")
        or _first_text(payload, "sender_name", "senderName")
    )
    sender_type = (
        _first_text(message, "sender_type", "senderType")
        or _first_text(sender, "sender_type", "senderType", "type")
        or _first_text(payload, "sender_type", "senderType")
    )
    external_thread_id = _first_text(message, "thread_id", "threadId") or _first_text(
        payload,
        "thread_id",
        "threadId",
    )
    source = _first_text(payload, "source") or _first_text(message, "source")
    return {
        "account_id": account_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_type": sender_type,
        "external_thread_id": external_thread_id,
        "source": source,
        "idempotency_key": f"openclaw:feishu:{account_id}:{chat_id}:{message_id}",
        "raw": payload,
    }


def parse_inbound_command(
    text: str,
    *,
    binding: OpenClawFeishuBridgeBindingConfig,
) -> Any:
    command = parse_feishu_agent_command(
        text,
        default_channel_id=binding.zaofu.channel_id,
        default_thread_id=binding.zaofu.thread_id or "main",
        require_prefix=binding.inbound.require_prefix,
        accept_plain_text=binding.inbound.accept_plain_text,
    )
    if (
        command.ok
        and binding.zaofu.channel_id
        and command.channel_id != binding.zaofu.channel_id
    ):
        return command.__class__(
            ok=False,
            status="rejected",
            reason="target channel_id does not match bridge binding",
        )
    return command


def _inbound_refs(
    binding: OpenClawFeishuBridgeBindingConfig,
    inbound: dict[str, Any],
    received: ZfEvent,
) -> dict[str, Any]:
    return {
        "bridge": {
            "id": binding.id,
            "source": BRIDGE_SOURCE,
            "inbound_event_id": received.id,
        },
        "openclaw": {
            "channel": "feishu",
            "account_id": inbound["account_id"],
            "chat_id": inbound["chat_id"],
            "message_id": inbound["message_id"],
            "thread_id": inbound["external_thread_id"],
            "sender_id": inbound["sender_id"],
            "sender_name": inbound["sender_name"],
        },
        "chat_id": inbound["chat_id"],
        "message_id": inbound["message_id"],
        "sender_id": inbound["sender_id"],
        "sender_name": inbound["sender_name"],
        "idempotency_key": inbound["idempotency_key"],
    }


def _emit_rejected(
    writer: EventWriter,
    *,
    binding: OpenClawFeishuBridgeBindingConfig,
    inbound: dict[str, Any],
    reason: str,
) -> ZfEvent:
    return writer.emit(
        BRIDGE_INBOUND_REJECTED,
        actor="zf-bridge",
        correlation_id=binding.zaofu.channel_id or inbound.get("chat_id") or None,
        payload=redact_obj({**_base_inbound_payload(binding, inbound), "reason": reason}),
    )


def _emit_loop_skipped(
    writer: EventWriter,
    *,
    binding: OpenClawFeishuBridgeBindingConfig,
    inbound: dict[str, Any],
    reason: str,
) -> ZfEvent:
    return writer.emit(
        BRIDGE_LOOP_SKIPPED,
        actor="zf-bridge",
        correlation_id=binding.zaofu.channel_id or inbound.get("chat_id") or None,
        payload=redact_obj({**_base_inbound_payload(binding, inbound), "reason": reason}),
    )


def _base_inbound_payload(
    binding: OpenClawFeishuBridgeBindingConfig,
    inbound: dict[str, Any],
) -> dict[str, Any]:
    return {
        "bridge_id": binding.id,
        "source": BRIDGE_SOURCE,
        "provider": "openclaw",
        "openclaw_channel": "feishu",
        "account_id": inbound.get("account_id") or "",
        "chat_id": inbound.get("chat_id") or "",
        "target": binding.feishu.target,
        "message_id": inbound.get("message_id") or "",
        "thread_id": inbound.get("external_thread_id") or "",
        "sender_id": inbound.get("sender_id") or "",
        "sender_name": inbound.get("sender_name") or "",
        "sender_type": inbound.get("sender_type") or "",
        "text": inbound.get("text") or "",
        "idempotency_key": inbound.get("idempotency_key") or "",
        "raw": inbound.get("raw") if isinstance(inbound.get("raw"), dict) else {},
    }


def _message_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for candidate in (
        payload.get("message"),
        _nested(payload, "result", "message"),
        _nested(payload, "result", "payload", "message"),
        _nested(payload, "payload", "message"),
        payload,
    ):
        if isinstance(candidate, dict):
            return candidate
    return payload


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text(payload: Any, *keys: str) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _coerce_message_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not (text.startswith("{") and text.endswith("}")):
        return text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        for key in ("text", "content", "message"):
            item = parsed.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return text


def _binding_chat_id(binding: OpenClawFeishuBridgeBindingConfig) -> str:
    return binding.feishu.chat_id or _target_chat_id(binding.feishu.target)


def _binding_allowed_chat_ids(binding: OpenClawFeishuBridgeBindingConfig) -> set[str]:
    allowed = {
        _target_chat_id(item)
        for item in [*binding.inbound.allowed_chat_ids, _binding_chat_id(binding)]
    }
    return {item for item in allowed if item}


def _target_chat_id(target: str) -> str:
    raw = str(target or "").strip()
    if raw.startswith("chat:"):
        return raw.split(":", 1)[1].strip()
    return raw


def _is_loop_message(inbound: dict[str, Any]) -> bool:
    text = str(inbound.get("text") or "").strip()
    sender_type = str(inbound.get("sender_type") or "").strip().lower()
    return (
        inbound.get("source") == BRIDGE_SOURCE
        or sender_type == "bot"
        or text.startswith("[ZaoFu:")
    )


def _has_seen_inbound(events: list[ZfEvent], idempotency_key: str, bridge_id: str) -> bool:
    for event in events:
        if event.type != BRIDGE_INBOUND_RECEIVED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if bridge_id and str(payload.get("bridge_id") or "") != bridge_id:
            continue
        if str(payload.get("idempotency_key") or "") == idempotency_key:
            return True
    return False


def _member_id(inbound: dict[str, Any]) -> str:
    sender_id = str(inbound.get("sender_id") or "").strip()
    if sender_id:
        return f"feishu:{sender_id}"
    sender_name = _safe_token(str(inbound.get("sender_name") or "").strip())
    return f"feishu:{sender_name or 'user'}"


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip())
    return token.strip("-") or "message"
