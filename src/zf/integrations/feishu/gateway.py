"""Feishu command gateway — normalize inputs to FeishuCommandEnvelope."""

from __future__ import annotations

import uuid
import shlex
import re
import hashlib
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
    form_values: dict[str, str] = field(default_factory=dict)
    attachments: list[dict[str, str]] = field(default_factory=list)
    action_token: str = ""
    attachment_message_id: str = ""


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
    # 2026-07-17 card-quality L3: acknowledge an owner-visible alert straight
    # from its card. Same event contract as the Web attention-ack action.
    "attention-ack": AuthLevel.OPERATOR,
    # Run Manager human-decision callbacks. Approving a controlled action needs
    # approver authority; diagnose/halt are operator-safe but still gated.
    "human-decision-approve": AuthLevel.APPROVER,
    "human-decision-diagnose": AuthLevel.OPERATOR,
    "human-decision-explain": AuthLevel.VIEWER,
    "human-decision-halt": AuthLevel.OPERATOR,
    "human-decision-reject": AuthLevel.OPERATOR,
    # Channel discussion owner-question callbacks. They resolve open
    # clarification questions, so require an operator-level owner identity.
    "channel-question-adopt": AuthLevel.OPERATOR,
    "channel-question-oos": AuthLevel.OPERATOR,
    "kanban-plan-answer": AuthLevel.OPERATOR,
    "kanban-proposal-approve": AuthLevel.APPROVER,
    "kanban-proposal-dismiss": AuthLevel.OPERATOR,
    "channel-progress-finalize": AuthLevel.OPERATOR,
    "channel-progress-confirm": AuthLevel.APPROVER,
    "channel-progress-create-task": AuthLevel.OPERATOR,
    "channel-progress-plan-workflow": AuthLevel.OPERATOR,
    # Issue Triage / Self-Issue adapter commands. Query is viewer-only;
    # mutations are additionally protected by signed action tokens at the CLI.
    "issue-summary": AuthLevel.VIEWER,
    "issue-list": AuthLevel.VIEWER,
    "issue-show": AuthLevel.VIEWER,
    "issue-triage-start": AuthLevel.OPERATOR,
    "issue-triage-cancel": AuthLevel.OPERATOR,
    "issue-fix-cancel": AuthLevel.OPERATOR,
    "issue-needs-info-submit": AuthLevel.OPERATOR,
    "issue-candidate-review": AuthLevel.OPERATOR,
    "issue-candidate-prepare": AuthLevel.APPROVER,
    "issue-candidate-pr": AuthLevel.APPROVER,
    "issue-candidate-pr-refresh": AuthLevel.OPERATOR,
    "issue-fix-proposal-approve": AuthLevel.APPROVER,
    "issue-fix-proposal-reject": AuthLevel.OPERATOR,
    "issue-fix-proposal-refresh": AuthLevel.OPERATOR,
    "issue-run-pause": AuthLevel.OPERATOR,
    "issue-run-resume": AuthLevel.OPERATOR,
    "issue-run-cancel": AuthLevel.OPERATOR,
    "self-issue-new": AuthLevel.OPERATOR,
    "self-issue-session-answer": AuthLevel.OPERATOR,
    "self-issue-session-confirm": AuthLevel.APPROVER,
    "self-issue-session-cancel": AuthLevel.OPERATOR,
    "self-issue-session-get": AuthLevel.VIEWER,
    "self-issue-session-retry": AuthLevel.OPERATOR,
    "self-issue-session-pause": AuthLevel.OPERATOR,
    "self-issue-session-reanalyze": AuthLevel.OPERATOR,
    "self-issue-session-materialize": AuthLevel.OPERATOR,
    "self-issue-session-activity-result": AuthLevel.VIEWER,
    "self-issue-github-device-start": AuthLevel.APPROVER,
    "self-issue-github-device-poll": AuthLevel.APPROVER,
    "self-issue-intake-start": AuthLevel.OPERATOR,
    "self-issue-get": AuthLevel.VIEWER,
    "self-issue-intake-get": AuthLevel.VIEWER,
    "self-issue-save": AuthLevel.OPERATOR,
    "self-issue-intake-save": AuthLevel.OPERATOR,
    "self-issue-submit": AuthLevel.OPERATOR,
    "self-issue-intake-submit": AuthLevel.OPERATOR,
    "self-issue-dismiss": AuthLevel.OPERATOR,
    "self-issue-intake-dismiss": AuthLevel.OPERATOR,
    "self-issue-preview": AuthLevel.OPERATOR,
    "self-issue-confirm": AuthLevel.APPROVER,
    "self-issue-publish": AuthLevel.APPROVER,
    "self-issue-recover": AuthLevel.OPERATOR,
    "self-issue-resolve-unknown": AuthLevel.OPERATOR,
    "self-issue-attachment-add": AuthLevel.OPERATOR,
    "self-issue-attachment-remove": AuthLevel.OPERATOR,
    "self-issue-intake-attachment-add": AuthLevel.OPERATOR,
    "self-issue-intake-attachment-remove": AuthLevel.OPERATOR,
    "self-issue-evidence-start": AuthLevel.OPERATOR,
    "self-issue-evidence-interrupt": AuthLevel.OPERATOR,
    "self-issue-evidence-resume": AuthLevel.OPERATOR,
    "self-issue-evidence-apply": AuthLevel.OPERATOR,
    "self-issue-evidence-fail": AuthLevel.OPERATOR,
    "self-issue-runtime-check": AuthLevel.VIEWER,
    "self-issue-limited-continue": AuthLevel.OPERATOR,
    "self-issue-attachment-preview": AuthLevel.OPERATOR,
    "self-issue-attachment-confirm": AuthLevel.OPERATOR,
    "self-issue-attachment-prepare": AuthLevel.OPERATOR,
    "self-issue-attachment-resolve-unknown": AuthLevel.OPERATOR,
}

_WHITELIST = set(_COMMAND_AUTH.keys())


def extract_zf_command_text(text: str) -> str:
    """Normalize explicit commands, including those after a leading @mention.

    Issue shorthand takes a literal description, not shell arguments. Quoting
    here preserves newlines, quotes and backslashes through the gateway parser.
    """
    value = str(text or "").strip()
    pattern = r"/zf |/issue(?=$|\s)"
    match = re.match(pattern, value)
    if match is None and value.startswith("@"):
        match = re.search(pattern, value)
    if match is None:
        return ""
    command = value[match.start():]
    if command.startswith("/zf "):
        return command
    description = command[len("/issue"):]
    return f"/zf self-issue-new {shlex.quote(description.strip())}"


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
        text = extract_zf_command_text(event.payload.get("text", ""))
        if not text:
            return None
        raw = text[4:].strip()
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()
        if not parts:
            return None
        command = parts[0]
        # Human-facing docs historically used ``/zf self-issue new`` while
        # the canonical allowlisted command is ``self-issue-new``. Normalize
        # that explicit two-token alias before whitelist and auth checks.
        if command == "self-issue" and len(parts) >= 2:
            command = f"self-issue-{parts[1]}"
            parts = [command, *parts[2:]]
        if command not in _WHITELIST:
            return None
        action_token = ""
        # A signed text reply supports clients without card forms. Only the
        # answer body is free text; the capability still binds session + chat.
        if command in {"self-issue-session-answer", "self-issue-session-confirm"} and len(parts) >= 4 and parts[2] == "--token":
            action_token = parts[3]
            parts = [parts[0], parts[1], *parts[4:]]
        message_id = _message_id(event)
        return FeishuCommandEnvelope(
            command=command,
            args=parts[1:],
            user_id=event.user_id,
            chat_id=event.chat_id,
            message_id=message_id,
            idempotency_key=_idempotency_key(event, "text", command, parts[1:]),
            source="text",
            action_token=action_token,
            attachment_message_id=str(
                event.payload.get("quote_message_id") or event.payload.get("parent_message_id") or ""
            ),
            attachments=list(event.payload.get("attachments") or [])
            if isinstance(event.payload.get("attachments"), list) else [],
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
        action_value = event.payload.get("action_value")
        action_token = str(event.payload.get("action_token") or "")
        if not action_token and isinstance(action_value, dict):
            action_token = str(action_value.get("t") or "")
        return FeishuCommandEnvelope(
            command=command,
            args=args,
            user_id=event.user_id,
            chat_id=event.chat_id,
            message_id=message_id,
            idempotency_key=(
                _idempotency_key(event, "button", command, args)
                + (":" + hashlib.sha256(action_token.encode()).hexdigest()[:24] if action_token else "")
            ),
            source="button",
            action_token=action_token,
            form_values={
                str(key): str(value)
                for key, value in (
                    event.payload.get("form_values", {}).items()
                    if isinstance(event.payload.get("form_values"), dict)
                    else []
                )
            },
            attachments=list(event.payload.get("attachments") or [])
            if isinstance(event.payload.get("attachments"), list) else [],
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
