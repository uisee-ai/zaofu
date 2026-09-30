"""A negative transport acknowledgement must never become a delivered receipt."""

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.integrations.feishu.projection import RoutingConfig
from zf.integrations.feishu.transport import FeishuTransport, MockFeishuTransport
from zf.runtime.owner_visible_delivery import deliver_owner_visible_messages_once


@pytest.mark.parametrize("card_raises", [False, True])
@pytest.mark.parametrize("accepted", [False, True])
def test_owner_delivery_honors_negative_transport_ack(tmp_path, card_raises, accepted):
    class Transport(MockFeishuTransport):
        def send_card(self, message):
            if card_raises:
                raise RuntimeError("card is unsupported")
            return FeishuTransport.send_card(self, message)

        def send_message(self, message):
            return accepted

    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    request = writer.append(ZfEvent(type="owner.visible_message.requested", payload={
        "message_id": "testkit-negative-ack", "severity": "high", "human_action_required": True,
        "title": "确认交付状态", "summary": "必须依据发送结果记账", "delivery_targets": ["feishu"],
    }))
    result = deliver_owner_visible_messages_once(
        event_log=log, writer=writer, transport=Transport(),
        routing=RoutingConfig(channels={"owner": "oc_testkit"}),
    )
    assert result.delivered == int(accepted)
    assert result.failed == int(not accepted)
    kind = "owner.visible_message.delivered" if accepted else "owner.visible_message.failed"
    [receipt] = [event for event in log.read_all() if event.type == kind]
    assert receipt.payload["source_event_id"] == request.id
    assert not [event for event in log.read_all() if event.type == (
        "owner.visible_message.failed" if accepted else "owner.visible_message.delivered"
    )]
