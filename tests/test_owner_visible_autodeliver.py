"""doc 78 O-7: opt-in auto-delivery of owner.visible_message to Feishu."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from zf.runtime.owner_visible_autodeliver import (
    deliver_owner_visible_to_feishu,
    owner_visible_autodeliver_enabled,
)


def test_disabled_without_env():
    assert owner_visible_autodeliver_enabled({}) is False
    assert owner_visible_autodeliver_enabled({"ZF_OWNER_VISIBLE_CHAT": "  "}) is False


def test_enabled_with_env():
    assert owner_visible_autodeliver_enabled({"ZF_OWNER_VISIBLE_CHAT": "oc_x"}) is True
    assert owner_visible_autodeliver_enabled({
        "ZF_OWNER_VISIBLE_APPROVAL_CHAT": "oc_approval",
    }) is True


def test_pytest_env_blocks_real_feishu_transport_construction(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "test fixture must not leave the process",
            "severity": "high", "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    with patch("zf.integrations.feishu.transport.FeishuHttpTransport") as transport:
        result = deliver_owner_visible_to_feishu(
            state_dir=tmp_path,
            env={
                "PYTEST_CURRENT_TEST": "tests/test_x.py::test_x (call)",
                "ZF_OWNER_VISIBLE_CHAT": "oc_real",
                "FEISHU_APP_ID": "cli_real",
                "FEISHU_APP_SECRET": "secret",
            },
        )

    assert result is None
    transport.assert_not_called()
    event_types = [
        json.loads(line)["type"]
        for line in events.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert event_types == ["owner.visible_message.requested"]


def test_fake_transport_is_allowed_even_when_live_feishu_is_disabled(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "fake delivery",
            "severity": "high", "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    transport = _FakeTransport()
    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={
            "ZF_DISABLE_LIVE_FEISHU": "1",
            "ZF_OWNER_VISIBLE_CHAT": "oc_fake",
        },
        transport=transport,
    )

    assert result is not None
    assert result.delivered == 1
    assert len(transport.sent) == 1


def test_stopped_runtime_suppresses_pending_non_human_owner_message(tmp_path):
    (tmp_path / "session.yaml").write_text(
        "runtime_state: stopped\nsession_id: sess-test\n",
        encoding="utf-8",
    )
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "title": "Cost budget exceeded",
            "summary": "budget exceeded; run manager will diagnose",
            "severity": "high", "human_action_required": True,
            "delivery_targets": ["feishu"],
            "human_action_required": False,
        },
    }) + "\n", encoding="utf-8")

    transport = _FakeTransport()
    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={"ZF_OWNER_VISIBLE_CHAT": "oc_fake"},
        transport=transport,
    )

    assert result is not None
    assert result.delivered == 0
    assert result.skipped == 1
    assert transport.sent == []
    suppressed = _event_payloads(events, "owner.visible_message.suppressed")
    assert suppressed[-1]["reason"] == "runtime_stopped"


def test_deliver_noop_when_unconfigured(tmp_path):
    # No ZF_OWNER_VISIBLE_CHAT → returns None, never touches a transport.
    result = deliver_owner_visible_to_feishu(state_dir=tmp_path, env={})
    assert result is None


def test_unconfigured_feishu_records_failed_receipt_for_pending_message(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "stall detected",
            "severity": "high", "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    result = deliver_owner_visible_to_feishu(state_dir=tmp_path, env={})

    assert result is not None
    assert result.failed == 1
    event_types = [
        json.loads(line)["type"]
        for line in events.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "owner.visible_message.delivery_attempted" in event_types
    assert "owner.visible_message.failed" in event_types
    failed = _event_payloads(events, "owner.visible_message.failed")
    assert failed[-1]["error_class"] == "feishu_route_unconfigured"
    assert "ZF_OWNER_VISIBLE_CHAT" in failed[-1]["action_hint"]


class _FakeTransport:
    def __init__(self):
        self.sent = []

    def send_message(self, message):
        self.sent.append(message)


class _FailingTransport:
    def send_message(self, message):
        raise RuntimeError("Feishu API error 400: open_id cross app")


def test_deliver_routes_owner_message_to_configured_chat(tmp_path):
    # A pending owner.visible_message.requested is delivered to the env chat via
    # the injected transport (the "owner" route maps to ZF_OWNER_VISIBLE_CHAT).
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {"message_id": "m1", "text": "stall detected", "severity": "high", "human_action_required": True},
    }) + "\n", encoding="utf-8")

    transport = _FakeTransport()
    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={"ZF_OWNER_VISIBLE_CHAT": "oc_owner_chat"},
        transport=transport,
    )
    assert result is not None
    assert len(transport.sent) == 1
    # routed to the configured owner chat
    assert transport.sent[0].chat_id == "oc_owner_chat"


def _event_payloads(path: Path, event_type: str) -> list[dict]:
    return [
        json.loads(line)["payload"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["type"] == event_type
    ]


def test_owner_chat_aliases_high_severity_to_approval_route(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "stall detected",
            "severity": "high", "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    transport = _FakeTransport()
    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={"ZF_OWNER_VISIBLE_CHAT": "oc_owner_chat"},
        transport=transport,
    )

    assert result is not None
    assert result.delivered == 1
    assert result.failed == 0
    assert len(transport.sent) == 1
    assert transport.sent[0].chat_id == "oc_owner_chat"
    attempted = _event_payloads(events, "owner.visible_message.delivery_attempted")
    assert attempted[-1]["route"] == "approval"
    assert attempted[-1]["receive_id"] == "oc_owner_chat"
    assert not _event_payloads(events, "owner.visible_message.failed")


def test_explicit_approval_chat_overrides_owner_alias(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "stale active run",
            "severity": "warn",
            "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    transport = _FakeTransport()
    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={
            "ZF_OWNER_VISIBLE_CHAT": "oc_owner_chat",
            "ZF_OWNER_VISIBLE_RECEIVE_ID_TYPE": "chat_id",
            "ZF_OWNER_VISIBLE_APPROVAL_CHAT": "ou_owner",
            "ZF_OWNER_VISIBLE_APPROVAL_RECEIVE_ID_TYPE": "open_id",
        },
        transport=transport,
    )

    assert result is not None
    assert result.delivered == 1
    assert result.failed == 0
    assert transport.sent[0].chat_id == "ou_owner"
    assert transport.sent[0].receive_id_type == "open_id"
    attempted = _event_payloads(events, "owner.visible_message.delivery_attempted")
    assert attempted[-1]["route"] == "approval"
    assert attempted[-1]["receive_id"] == "ou_owner"
    assert attempted[-1]["receive_id_type"] == "open_id"


def test_owner_visible_preflights_receive_id_type_prefix_mismatch(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "stale active run",
            "severity": "warn",
            "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    transport = _FakeTransport()
    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={"ZF_OWNER_VISIBLE_CHAT": "ou_owner"},
        transport=transport,
    )

    assert result is not None
    assert result.failed == 1
    assert transport.sent == []
    failed = _event_payloads(events, "owner.visible_message.failed")
    assert failed[-1]["error_class"] == "feishu_receive_id_type_mismatch"
    assert "receive_id_type" in failed[-1]["action_hint"]


def test_owner_visible_classifies_open_id_cross_app_failure(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "needs owner",
            "severity": "high", "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={
            "ZF_OWNER_VISIBLE_APPROVAL_CHAT": "ou_owner",
            "ZF_OWNER_VISIBLE_APPROVAL_RECEIVE_ID_TYPE": "open_id",
        },
        transport=_FailingTransport(),
    )

    assert result is not None
    assert result.failed == 1
    failed = _event_payloads(events, "owner.visible_message.failed")
    assert failed[-1]["error_class"] == "feishu_open_id_cross_app"
    assert "configured Feishu app" in failed[-1]["action_hint"]


def test_owner_visible_cross_app_failure_marks_route_unhealthy_once(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {
            "message_id": "m1",
            "text": "needs owner",
            "severity": "high", "human_action_required": True,
            "delivery_targets": ["feishu"],
        },
    }) + "\n", encoding="utf-8")

    first = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={
            "ZF_OWNER_VISIBLE_APPROVAL_CHAT": "ou_owner",
            "ZF_OWNER_VISIBLE_APPROVAL_RECEIVE_ID_TYPE": "open_id",
        },
        transport=_FailingTransport(),
    )
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "id": "evt-2",
            "type": "owner.visible_message.requested",
            "actor": "supervisor",
            "payload": {
                "message_id": "m2",
                "text": "needs owner again",
                "severity": "high", "human_action_required": True,
                "delivery_targets": ["feishu"],
            },
        }) + "\n")

    transport = _FakeTransport()
    second = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={
            "ZF_OWNER_VISIBLE_APPROVAL_CHAT": "ou_owner",
            "ZF_OWNER_VISIBLE_APPROVAL_RECEIVE_ID_TYPE": "open_id",
        },
        transport=transport,
    )

    assert first is not None
    assert first.failed == 1
    assert second is not None
    assert second.attempted == 0
    assert second.skipped == 2
    assert transport.sent == []
    event_types = [
        json.loads(line)["type"]
        for line in events.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert event_types.count("owner.visible_message.delivery_attempted") == 1
    assert event_types.count("owner.visible_message.failed") == 1
    assert event_types.count("owner.visible_message.route_unhealthy") == 1


def test_deliver_constructs_concrete_transport_when_none_injected(tmp_path, monkeypatch):
    # R13 regression (backlog 2026-06-06-0401 §E): the default branch built the
    # ABSTRACT ``FeishuTransport()`` → TypeError → silent no-op since the
    # transport was made abstract. It must construct the concrete
    # FeishuHttpTransport. The other tests always INJECT a transport, so this
    # default-construction branch went untested and the abstract bug shipped.
    import zf.integrations.feishu.transport as ftrans

    constructed = []

    class _FakeHttp:
        def __init__(self):
            constructed.append(self)
            self.sent = []

        def send_message(self, message):
            self.sent.append(message)

    monkeypatch.setattr(ftrans, "FeishuHttpTransport", _FakeHttp)

    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "id": "evt-1",
        "type": "owner.visible_message.requested",
        "actor": "supervisor",
        "payload": {"message_id": "m1", "text": "stall", "severity": "high", "human_action_required": True},
    }) + "\n", encoding="utf-8")

    # No transport injected → exercises the default-construction branch.
    result = deliver_owner_visible_to_feishu(
        state_dir=tmp_path,
        env={
            "ZF_ALLOW_LIVE_FEISHU_IN_TESTS": "1",
            "ZF_OWNER_VISIBLE_CHAT": "oc_owner_chat",
        },
    )
    assert result is not None            # not None from a construction TypeError
    assert len(constructed) == 1         # concrete transport constructed
    assert len(constructed[0].sent) == 1  # and actually delivered
