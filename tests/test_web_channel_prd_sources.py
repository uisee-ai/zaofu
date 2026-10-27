"""Source previews are bound to the same Channel, Owner and snapshot."""
from zf.core.events import EventLog, EventWriter
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.web.channel_prd_sources import prd_source_view


def test_file_source_preview_requires_matching_scoped_request(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    descriptor = write_sidecar_text(tmp_path, "channels/prd-sources/source.md", "原始文档完整正文",
        kind="channel_prd_source", schema_version="channel.prd-source.v1", content_type="text/markdown",
        created_by="owner", access_scope={"channel_id": "ch", "thread_id": "main"})
    writer.emit("channel.synthesis.requested", actor="web", correlation_id="ch", payload={
        "channel_id": "ch", "thread_id": "main", "request_id": "req", "prd_source_file": descriptor})
    channel = {"channel_id": "ch", "owner_actor_ref": "web", "messages": []}
    synthesis = {"source_coverage": [{"kind": "channel_prd_source", "artifact_ref": descriptor["ref"],
                                      "artifact_digest": descriptor["sha256"]}]}
    preview = prd_source_view(tmp_path, channel, writer.event_log.read_all(), "main", synthesis)
    assert preview["status"] == "ready"
    assert preview["items"][0]["content"] == "原始文档完整正文"
    wrong_thread = prd_source_view(tmp_path, channel, writer.event_log.read_all(), "other", synthesis)
    assert wrong_thread == {"status": "incomplete", "items": []}
    (tmp_path / descriptor["ref"]).write_text("tampered")
    broken = prd_source_view(tmp_path, channel, writer.event_log.read_all(), "main", synthesis)
    assert broken["status"] == "incomplete"
    assert not broken["items"][0]["available"]


def test_non_owner_message_cannot_be_exposed_as_owner_source(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    event = writer.emit("channel.message.posted", actor="another-member", correlation_id="ch", payload={
        "channel_id": "ch", "thread_id": "main", "message_id": "m", "role": "user", "text": "private"})
    channel = {"channel_id": "ch", "owner_actor_ref": "web", "messages": [{
        "message_id": "m", "event_id": event.id, "thread_id": "main", "role": "user", "text": "private"}]}
    synthesis = {"source_coverage": [{"kind": "owner_input", "message_id": "m", "message_body_digest": "fake"}]}
    preview = prd_source_view(tmp_path, channel, writer.event_log.read_all(), "main", synthesis)
    assert preview == {"status": "incomplete", "items": []}
