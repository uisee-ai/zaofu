"""Tests for Feishu transport parsing and HTTP request shape."""

from __future__ import annotations

import json

from zf.integrations.feishu.transport import FeishuHttpTransport, FeishuMessage


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_parse_feishu_v2_message_event():
    transport = FeishuHttpTransport(tenant_access_token="token")
    event = transport.parse_webhook({
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "event_id": "evt-1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": json.dumps({"text": "/zf status"}),
            },
        },
    })

    assert event is not None
    assert event.event_type == "message"
    assert event.user_id == "ou_sender"
    assert event.chat_id == "oc_1"
    assert event.payload["text"] == "/zf status"
    assert event.payload["message_id"] == "om_1"


def test_parse_feishu_v2_message_event_preserves_reply_aliases():
    transport = FeishuHttpTransport(tenant_access_token="token")
    event = transport.parse_webhook({
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "event_id": "evt-1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}},
            "message": {
                "message_id": "om_child",
                "chat_id": "oc_1",
                "content": json.dumps({"text": "reply"}),
                "parent_id": "om_parent",
                "root_id": "om_root",
                "quote_id": "om_quote",
                "thread_id": "thread-1",
            },
        },
    })

    assert event is not None
    assert event.payload["parent_message_id"] == "om_parent"
    assert event.payload["root_message_id"] == "om_root"
    assert event.payload["quote_message_id"] == "om_quote"
    assert event.payload["thread_id"] == "thread-1"


def test_parse_card_action_uses_context_chat_and_message_id():
    transport = FeishuHttpTransport(tenant_access_token="token")
    event = transport.parse_webhook({
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "event_id": "evt-card"},
        "event": {
            "action": {"value": {"action": "human-decision-approve:hdec-1"}},
            "operator": {"operator_id": {"open_id": "ou_owner"}},
            "context": {
                "open_chat_id": "oc_1",
                "open_message_id": "om_card",
            },
        },
    })

    assert event is not None
    assert event.event_type == "button_action"
    assert event.chat_id == "oc_1"
    assert event.payload["message_id"] == "om_card"
    assert event.payload["open_message_id"] == "om_card"


def test_real_transport_send_message_supports_open_id_receive_type():
    requests = []

    def fake_urlopen(request, timeout=15):
        requests.append(request)
        return FakeResponse({"code": 0, "msg": "ok"})

    transport = FeishuHttpTransport(
        base_url="https://open.feishu.cn/open-apis",
        tenant_access_token="tenant-token",
        request_func=fake_urlopen,
    )

    assert transport.send_message(FeishuMessage(
        chat_id="ou_07bf51fbbd81df6de99e2f327bbc2d59",
        content="hello",
        receive_id_type="open_id",
    ))

    request = requests[0]
    assert request.full_url.endswith("/im/v1/messages?receive_id_type=open_id")
    assert request.headers["Authorization"] == "Bearer tenant-token"
    body = json.loads(request.data.decode("utf-8"))
    assert body["receive_id"] == "ou_07bf51fbbd81df6de99e2f327bbc2d59"
    assert body["msg_type"] == "text"
    assert json.loads(body["content"])["text"] == "hello"
