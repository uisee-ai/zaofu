"""Feishu command gateway — normalize inputs to FeishuCommandEnvelope."""

from __future__ import annotations

import uuid
import shlex
from dataclasses import dataclass, field
from enum import Enum

from zf.integrations.feishu.transport import FeishuWebhookEvent


class AuthLevel(Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"


@dataclass
class FeishuCommandEnvelope:
    command: str
    args: list[str] = field(default_factory=list)
    user_id: str = ""
    chat_id: str = ""
    message_id: str = ""
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    source: str = "text"  # text, button, approval


# Commands and their required auth levels
_COMMAND_AUTH: dict[str, AuthLevel] = {
    "status": AuthLevel.VIEWER,
    "tasks": AuthLevel.VIEWER,
    "task": AuthLevel.VIEWER,
    "cost": AuthLevel.VIEWER,
    "blockers": AuthLevel.VIEWER,
    "workers": AuthLevel.VIEWER,
    "handoff": AuthLevel.VIEWER,
    "ask": AuthLevel.VIEWER,
    "attention": AuthLevel.OPERATOR,
    "create": AuthLevel.OPERATOR,
    "update": AuthLevel.OPERATOR,
    "request-fanout": AuthLevel.OPERATOR,
    "pause": AuthLevel.OPERATOR,
    "resume": AuthLevel.OPERATOR,
    "retry": AuthLevel.OPERATOR,
    "cancel": AuthLevel.OPERATOR,
    "assign": AuthLevel.OPERATOR,
    "note": AuthLevel.OPERATOR,
    "approve": AuthLevel.APPROVER,
    "deny": AuthLevel.APPROVER,
    # feishu-A P0.3: plan approval callbacks (one-click approve in Feishu;
    # reject needs a reason → routed to Web). Mutations gated by feishu-B.
    "plan-approve": AuthLevel.APPROVER,
    "plan-reject": AuthLevel.APPROVER,
    # feishu-C: Interrupt a running channel reply (headless cancel, no tmux).
    "agent-cancel": AuthLevel.OPERATOR,
    # Run Manager human-decision callbacks. Approving a controlled action needs
    # approver authority; diagnose/halt are operator-safe but still gated.
    "human-decision-approve": AuthLevel.APPROVER,
    "human-decision-diagnose": AuthLevel.OPERATOR,
    "human-decision-halt": AuthLevel.OPERATOR,
    "human-decision-reject": AuthLevel.OPERATOR,
    # Channel discussion owner-question callbacks. They resolve open
    # clarification questions, so require an operator-level owner identity.
    "channel-question-adopt": AuthLevel.OPERATOR,
    "channel-question-oos": AuthLevel.OPERATOR,
}

_WHITELIST = set(_COMMAND_AUTH.keys())


class CommandGateway:
    """Normalize Feishu inputs into command envelopes."""

    def __init__(self, *, user_levels: dict[str, AuthLevel] | None = None) -> None:
        self.user_levels = user_levels or {}
        self._seen_keys: set[str] = set()

    def parse(self, event: FeishuWebhookEvent) -> FeishuCommandEnvelope | None:
        """Parse a webhook event into a command envelope."""
        if event.event_type == "message":
            return self._parse_text(event)
        if event.event_type == "button_action":
            return self._parse_button(event)
        return None

    def is_authorized(self, envelope: FeishuCommandEnvelope) -> bool:
        """Check if user has permission for this command."""
        required = _COMMAND_AUTH.get(envelope.command, AuthLevel.OPERATOR)
        user_level = self.user_levels.get(envelope.user_id, AuthLevel.VIEWER)
        level_order = [AuthLevel.VIEWER, AuthLevel.OPERATOR, AuthLevel.APPROVER]
        return level_order.index(user_level) >= level_order.index(required)

    def is_duplicate(self, envelope: FeishuCommandEnvelope) -> bool:
        """Check if this command was already processed."""
        if envelope.idempotency_key in self._seen_keys:
            return True
        self._seen_keys.add(envelope.idempotency_key)
        return False

    def _parse_text(self, event: FeishuWebhookEvent) -> FeishuCommandEnvelope | None:
        text = event.payload.get("text", "").strip()
        if not text.startswith("/zf "):
            return None
        raw = text[4:].strip()
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()
        if not parts:
            return None
        command = parts[0]
        if command not in _WHITELIST:
            return None
        message_id = _message_id(event)
        return FeishuCommandEnvelope(
            command=command,
            args=parts[1:],
            user_id=event.user_id,
            chat_id=event.chat_id,
            message_id=message_id,
            idempotency_key=_idempotency_key(event, "text", command, parts[1:]),
            source="text",
        )

    def _parse_button(self, event: FeishuWebhookEvent) -> FeishuCommandEnvelope | None:
        action = event.payload.get("action", "")
        if not action:
            return None
        parts = action.split(":")
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        if command not in _WHITELIST:
            return None
        message_id = _message_id(event)
        return FeishuCommandEnvelope(
            command=command,
            args=args,
            user_id=event.user_id,
            chat_id=event.chat_id,
            message_id=message_id,
            idempotency_key=_idempotency_key(event, "button", command, args),
            source="button",
        )


def _message_id(event: FeishuWebhookEvent) -> str:
    for key in ("message_id", "open_message_id", "event_id", "action_id"):
        value = event.payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _idempotency_key(
    event: FeishuWebhookEvent,
    source: str,
    command: str,
    args: list[str],
) -> str:
    message_id = _message_id(event)
    if message_id:
        args_key = ":".join(args)
        suffix = f":{args_key}" if args_key else ""
        return f"feishu:{source}:{message_id}:{command}{suffix}"
    return uuid.uuid4().hex
