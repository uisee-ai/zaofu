"""feishu-C #2: reply routes back to origin Feishu chat (refs.feishu/openclaw)."""

from __future__ import annotations

from dataclasses import replace

from zf.core.config.schema import (
    OpenClawFeishuBridgeBindingConfig,
    OpenClawFeishuBridgeFeishuConfig,
    OpenClawFeishuBridgeOutboundConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.channel_adapter import _origin_external_refs
from zf.runtime.openclaw_feishu_bridge import _event_feishu_target


def _binding(reply_to_inbound_source: bool = True) -> OpenClawFeishuBridgeBindingConfig:
    return OpenClawFeishuBridgeBindingConfig(
        feishu=OpenClawFeishuBridgeFeishuConfig(target="chat:default"),
        outbound=OpenClawFeishuBridgeOutboundConfig(
            reply_to_inbound_source=reply_to_inbound_source),
    )


def _reply(refs: dict, source: str = "fake") -> ZfEvent:
    return ZfEvent(type="channel.message.posted", actor="dev",
                   payload={"channel_id": "ch", "text": "hi", "source": source,
                            "refs": refs})


# --- targeting standardizes refs.feishu / refs.openclaw, source-agnostic -----

def test_reply_with_feishu_origin_routes_back_even_if_backend_source():
    # the key #2 fix: a channel agent reply (source=backend) carrying the origin
    # feishu chat routes back there, not to the default target.
    ev = _reply({"feishu": {"chat_id": "oc_origin"}}, source="fake")
    assert _event_feishu_target(ev, bridge_binding=_binding()) == "chat:oc_origin"


def test_reply_with_openclaw_origin_routes_back():
    ev = _reply({"openclaw": {"chat_id": "oc_oc"}}, source="fake")
    assert _event_feishu_target(ev, bridge_binding=_binding()) == "chat:oc_oc"


def test_reply_without_origin_falls_back_to_bound_target():
    ev = _reply({"request_id": "r1"}, source="fake")
    assert _event_feishu_target(ev, bridge_binding=_binding()) == "chat:default"


def test_reply_to_inbound_source_disabled_uses_default():
    ev = _reply({"feishu": {"chat_id": "oc_origin"}})
    assert _event_feishu_target(
        ev, bridge_binding=_binding(reply_to_inbound_source=False)) == "chat:default"


def test_legacy_source_gated_path_still_works():
    ev = ZfEvent(type="channel.message.posted", actor="a",
                 payload={"source": "feishu_agent", "refs": {"chat_id": "oc_legacy"}})
    assert _event_feishu_target(ev, bridge_binding=_binding()) == "chat:oc_legacy"


# --- reply propagation carries the triggering message's external origin ------

def test_origin_refs_extracted_from_triggering_message():
    msg = {"refs": {"feishu": {"chat_id": "oc_x", "message_id": "om1"},
                    "request_id": "r", "run_id": "run"}}
    assert _origin_external_refs(msg) == {"feishu": {"chat_id": "oc_x",
                                                     "message_id": "om1"}}


def test_origin_refs_empty_when_no_external_namespace():
    assert _origin_external_refs({"refs": {"request_id": "r"}}) == {}
    assert _origin_external_refs({}) == {}


# --- #1: channel projection exposes external origin for the Web source chip --

def test_projection_message_carries_external_origin():
    from zf.runtime.channel_projection import _apply_message
    channel = {"messages": {}, "threads": {}}
    event = ZfEvent(type="channel.message.posted", actor="ou_x",
                    payload={"channel_id": "ch", "message_id": "m1",
                             "text": "hi", "member_id": "feishu",
                             "refs": {"feishu": {"chat_id": "oc_origin",
                                                 "message_id": "om1"}}})
    _apply_message(channel, event, event.payload)
    assert channel["messages"]["m1"]["origin"] == {
        "channel": "feishu", "chat_id": "oc_origin"}


def test_projection_web_native_message_has_empty_origin():
    from zf.runtime.channel_projection import _apply_message
    channel = {"messages": {}, "threads": {}}
    event = ZfEvent(type="channel.message.posted", actor="operator",
                    payload={"channel_id": "ch", "message_id": "m2",
                             "text": "web msg", "member_id": "operator"})
    _apply_message(channel, event, event.payload)
    assert channel["messages"]["m2"]["origin"] == {}
