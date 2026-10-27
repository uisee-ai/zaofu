"""Controlled web owner identity is scoped to a channel and thread."""
from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_owner_sources import is_owner_message


def test_web_operator_identity_does_not_promote_other_senders(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    def message(actor, *, source="web", member="operator", thread="main", role="user"):
        return writer.emit("channel.message.posted", actor=actor, correlation_id="ch", payload={
            "channel_id": "ch", "thread_id": thread, "source": source, "member_id": member, "role": role})
    def allowed(event):
        return is_owner_message(event, channel_id="ch", thread_id="main", owner="owner:operator")
    assert allowed(message("web"))
    assert allowed(message("owner:operator", source="cli"))
    assert not allowed(message("agent"))
    assert not allowed(message("web", member="other"))
    assert not allowed(message("web", source="feishu"))
    assert not allowed(message("web", thread="other"))
    assert not allowed(message("web", role="assistant"))
