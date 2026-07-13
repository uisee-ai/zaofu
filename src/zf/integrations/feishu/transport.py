"""Feishu transport adapter — webhook receive + message send."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeishuMessage:
    chat_id: str
    content: str
    msg_type: str = "text"  # text, interactive
    thread_id: str | None = None
    receive_id_type: str = "chat_id"


@dataclass
class FeishuWebhookEvent:
    event_type: str  # message, button_action, approval
    payload: dict = field(default_factory=dict)
    user_id: str = ""
    chat_id: str = ""


class FeishuTransportError(RuntimeError):
    """Raised when the real Feishu transport cannot complete a request."""


# doc 79 Tier3: tenant_access_token-invalid codes. A cached token expires
# (~2h); R12 ran 8.5h and kept resending the stale token → 99991663. On these
# codes the transport invalidates the cached token and re-mints once.
TOKEN_INVALID_CODES = frozenset({
    99991663,  # invalid access token
    99991664,  # invalid access token (tenant)
    99991668,  # access token expired
    99991661,  # access token is empty
})


def is_token_invalid_code(code: int) -> bool:
    try:
        return int(code) in TOKEN_INVALID_CODES
    except (TypeError, ValueError):
        return False


def _error_body_code(detail: str) -> int:
    """Best-effort extract Feishu ``code`` from an HTTP error body (JSON)."""
    try:
        data = json.loads(detail)
        return int(data.get("code") or 0)
    except (ValueError, TypeError, AttributeError):
        return 0


class FeishuTransport(ABC):
    """Abstract transport for Feishu API interactions."""

    @abstractmethod
    def send_message(self, message: FeishuMessage) -> bool:
        """Send a message. Returns True on success."""

    @abstractmethod
    def update_message(self, message_id: str, content: str) -> bool:
        """Update an existing message."""

    @abstractmethod
    def parse_webhook(self, data: dict) -> FeishuWebhookEvent | None:
        """Parse incoming webhook payload."""

    def send_card(self, message: FeishuMessage) -> str | None:
        """Send an interactive card; return the provider message_id (needed to
        update the card on a later verdict). Default best-effort sends but
        cannot surface an id — subclasses that can, override."""
        self.send_message(message)
        return None

    def update_card(self, message_id: str, card: dict, sequence: int = 0) -> bool:
        """Update an interactive card message in place. Default delegates to the
        text update path; subclasses with a card-patch endpoint override.

        ``sequence`` (feishu-stream B2): a monotonic per-card counter so the
        CardKit server can reject/reorder stale streaming updates. 0 = unset
        (non-streaming callers); base/text path ignores it."""
        return self.update_message(message_id, json.dumps(card, ensure_ascii=False))

    def list_recent(self, chat_id: str, *, page_size: int = 50) -> list[dict]:
        """Recent messages in a chat, newest-first, normalized to
        {message_id, chat_id, msg_type, content, create_time, sender, mentions,
        chat_type}. Used by catchup-on-restart (W5). Default: none."""
        return []

    def bot_open_id(self) -> str:
        """This app's bot open_id, for "was I the @-target" filtering in a
        multi-bot group. Empty when unknown (callers fail open)."""
        return ""


class MockFeishuTransport(FeishuTransport):
    """Mock transport for testing — records all operations."""

    def __init__(self) -> None:
        self.sent_messages: list[FeishuMessage] = []
        self.updated_messages: list[tuple[str, str]] = []
        self.updated_sequences: list[tuple[str, int]] = []
        self.recent_messages: list[dict] = []  # catchup: inject list_recent rows
        self.bot_open_id_value: str = ""       # mention-filter: inject this bot's id

    def send_message(self, message: FeishuMessage) -> bool:
        self.sent_messages.append(message)
        return True

    def update_message(self, message_id: str, content: str) -> bool:
        self.updated_messages.append((message_id, content))
        return True

    def send_card(self, message: FeishuMessage) -> str | None:
        self.sent_messages.append(message)
        return f"mock-msg-{len(self.sent_messages)}"

    def update_card(self, message_id: str, card: dict, sequence: int = 0) -> bool:
        self.updated_messages.append((message_id, json.dumps(card, ensure_ascii=False)))
        self.updated_sequences.append((message_id, sequence))
        return True

    def parse_webhook(self, data: dict) -> FeishuWebhookEvent | None:
        return parse_webhook_payload(data)

    def list_recent(self, chat_id: str, *, page_size: int = 50) -> list[dict]:
        return list(self.recent_messages)

    def bot_open_id(self) -> str:
        return self.bot_open_id_value


class FeishuHttpTransport(FeishuTransport):
    """Minimal Feishu OpenAPI transport using tenant access token auth."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        tenant_access_token: str | None = None,
        app_id: str | None = None,
        app_secret: str | None = None,
        request_func: Any | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("FEISHU_BASE_URL")
                         or "https://open.feishu.cn/open-apis").rstrip("/")
        self.tenant_access_token = tenant_access_token or os.environ.get(
            "FEISHU_TENANT_ACCESS_TOKEN",
            "",
        )
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self._request_func = request_func or urllib.request.urlopen

    def send_message(self, message: FeishuMessage) -> bool:
        receive_id_type = message.receive_id_type or "chat_id"
        body = {
            "receive_id": message.chat_id,
            "msg_type": message.msg_type,
            "content": _message_content(message),
        }
        self._request_json(
            "POST",
            f"/im/v1/messages?receive_id_type={urllib.parse.quote(receive_id_type)}",
            body,
        )
        return True

    def update_message(self, message_id: str, content: str) -> bool:
        body = {
            "msg_type": "text",
            "content": json.dumps({"text": content}, ensure_ascii=False),
        }
        self._request_json("PUT", f"/im/v1/messages/{message_id}", body)
        return True

    def send_card(self, message: FeishuMessage) -> str | None:
        receive_id_type = message.receive_id_type or "chat_id"
        body = {
            "receive_id": message.chat_id,
            "msg_type": "interactive",
            "content": _message_content(message),
        }
        resp = self._request_json(
            "POST",
            f"/im/v1/messages?receive_id_type={urllib.parse.quote(receive_id_type)}",
            body,
        )
        message_id = str((resp.get("data") or {}).get("message_id") or "")
        return message_id or None

    def update_card(self, message_id: str, card: dict, sequence: int = 0) -> bool:
        body = {"content": json.dumps(card, ensure_ascii=False)}
        # feishu-stream B2: carry the monotonic sequence for CardKit streaming
        # updates so the server can drop/reorder stale frames. Omitted when 0
        # (non-streaming in-place updates keep the plain text-PATCH shape).
        if sequence:
            body["sequence"] = sequence
        self._request_json("PATCH", f"/im/v1/messages/{message_id}", body)
        return True

    def list_recent(self, chat_id: str, *, page_size: int = 50) -> list[dict]:
        """GET /im/v1/messages newest-first, normalized for catchup (W5)."""
        path = (f"/im/v1/messages?container_id_type=chat"
                f"&container_id={urllib.parse.quote(chat_id)}"
                f"&page_size={int(page_size)}&sort_type=ByCreateTimeDesc")
        resp = self._request_json("GET", path, None)
        items = (resp.get("data") or {}).get("items") or []
        rows: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
            body = item.get("body") if isinstance(item.get("body"), dict) else {}
            mentions = [str(m.get("id") or "") for m in (item.get("mentions") or [])
                        if isinstance(m, dict) and m.get("id")]
            rows.append({
                "message_id": str(item.get("message_id") or ""),
                "chat_id": str(item.get("chat_id") or chat_id),
                "msg_type": str(item.get("msg_type") or "text"),
                "content": _content_text(body.get("content")),
                "create_time": str(item.get("create_time") or ""),
                "sender": {"id": str(sender.get("id") or ""),
                           "sender_type": str(sender.get("sender_type")
                                              or sender.get("id_type") or "")},
                "mentions": mentions,
                "chat_type": str(item.get("chat_type") or ""),
            })
        return rows

    def bot_open_id(self) -> str:
        if getattr(self, "_bot_open_id", None) is None:
            try:
                resp = self._request_json("GET", "/bot/v3/info", None)
                self._bot_open_id = str((resp.get("bot") or {}).get("open_id") or "")
            except Exception:  # noqa: BLE001 — unknown id → fail open (reply)
                self._bot_open_id = ""
        return self._bot_open_id

    def parse_webhook(self, data: dict) -> FeishuWebhookEvent | None:
        return parse_webhook_payload(data)

    def _tenant_token(self) -> str:
        if self.tenant_access_token:
            return self.tenant_access_token
        if not self.app_id or not self.app_secret:
            raise FeishuTransportError(
                "FEISHU_TENANT_ACCESS_TOKEN or FEISHU_APP_ID/FEISHU_APP_SECRET is required",
            )
        response = self._request_json(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
            auth=False,
        )
        token = str(response.get("tenant_access_token") or "")
        if not token:
            raise FeishuTransportError("Feishu token response did not include tenant_access_token")
        self.tenant_access_token = token
        return token

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict | None,
        *,
        auth: bool = True,
        _retried: bool = False,
    ) -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self._tenant_token()}"
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with self._request_func(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # doc 79 Tier3: cached tenant_access_token went stale → invalidate
            # and re-mint once, rather than resending the dead token forever.
            if (
                auth
                and not _retried
                and is_token_invalid_code(_error_body_code(detail))
            ):
                self.tenant_access_token = ""
                return self._request_json(
                    method, path, body, auth=auth, _retried=True
                )
            raise FeishuTransportError(f"Feishu HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise FeishuTransportError(f"Feishu request failed: {exc}") from exc
        if not raw:
            return {}
        data = json.loads(raw)
        if isinstance(data, dict) and int(data.get("code") or 0) != 0:
            code = int(data.get("code") or 0)
            if auth and not _retried and is_token_invalid_code(code):
                self.tenant_access_token = ""
                return self._request_json(
                    method, path, body, auth=auth, _retried=True
                )
            raise FeishuTransportError(
                f"Feishu API error {data.get('code')} on {method} {path}: {data.get('msg')}",
            )
        return data if isinstance(data, dict) else {}


def parse_webhook_payload(data: dict) -> FeishuWebhookEvent | None:
    """Parse local fixtures and Feishu v2 event callbacks."""
    event_type = data.get("type", "")
    if event_type:
        return FeishuWebhookEvent(
            event_type=event_type,
            payload=data.get("payload", {}),
            user_id=data.get("user_id", ""),
            chat_id=data.get("chat_id", ""),
        )

    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    feishu_event_type = str(header.get("event_type") or "")
    if feishu_event_type == "im.message.receive_v1":
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
        sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
        text = _content_text(message.get("content"))
        message_id = str(message.get("message_id") or "")
        mentions = [
            str(item.get("id") or item.get("open_id") or "")
            for item in (message.get("mentions") or [])
            if isinstance(item, dict)
        ]
        return FeishuWebhookEvent(
            event_type="message",
            payload={
                "text": text,
                "message_id": message_id,
                "parent_message_id": str(
                    message.get("parent_message_id") or message.get("parent_id") or ""
                ),
                "root_message_id": str(
                    message.get("root_message_id") or message.get("root_id") or ""
                ),
                "quote_message_id": str(
                    message.get("quote_message_id") or message.get("quote_id") or ""
                ),
                "thread_id": str(message.get("thread_id") or ""),
                "mentions": mentions,
                "app_id": str(header.get("app_id") or header.get("app_id_v2") or ""),
                "raw": event,
            },
            user_id=str(sender_id.get("open_id") or sender_id.get("user_id") or ""),
            chat_id=str(message.get("chat_id") or ""),
        )

    if feishu_event_type in {"card.action.trigger", "application.bot.menu_v6"}:
        action = event.get("action") if isinstance(event.get("action"), dict) else {}
        value = action.get("value") if isinstance(action.get("value"), dict) else {}
        operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
        operator_id = operator.get("operator_id") if isinstance(operator.get("operator_id"), dict) else {}
        context = event.get("context") if isinstance(event.get("context"), dict) else {}
        chat_id = str(event.get("open_chat_id") or context.get("open_chat_id") or "")
        message_id = str(
            event.get("open_message_id")
            or context.get("open_message_id")
            or header.get("event_id")
            or ""
        )
        return FeishuWebhookEvent(
            event_type="button_action",
            payload={
                "action": str(value.get("action") or action.get("action") or ""),
                # feishu-A2: surface the full button value so a signed action
                # token (value.t) survives to the handler — the normalized path
                # otherwise drops everything but .action.
                "action_value": value,
                "action_token": str(value.get("t") or ""),
                "message_id": message_id,
                "open_message_id": message_id,
                "action_id": str(action.get("tag") or header.get("event_id") or ""),
                "raw": event,
            },
            user_id=str(operator_id.get("open_id") or operator_id.get("user_id") or ""),
            chat_id=chat_id,
        )

    return None


def _message_content(message: FeishuMessage) -> str:
    if message.msg_type == "text":
        return json.dumps({"text": message.content}, ensure_ascii=False)
    return message.content


def _content_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if not isinstance(content, str):
        return ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content
    return str(data.get("text") or "") if isinstance(data, dict) else content
