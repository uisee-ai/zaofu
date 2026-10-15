"""Uploaded requirement text is complete and verified in deliberation contexts."""
from pathlib import Path

import pytest

from zf.runtime.channel_attachments import capture_channel_attachment
from zf.runtime.channel_semantic_sources import build_semantic_source_bundle


@pytest.mark.parametrize("fault", ["", "corrupt", "scope", "budget", "trigger"])
def test_restart_and_synthesis_include_verified_attachment_body(tmp_path: Path, fault: str):
    text = "# 原始需求\nPostgreSQL、Doris 暂缓、键盘操作。\nEND-OF-SOURCE\n"
    attachment = capture_channel_attachment(tmp_path, channel_id="ch", thread_id="main",
        name="PRD.md", mime="text/markdown", data=text.encode())
    if fault == "corrupt":
        (tmp_path / attachment["body_ref"]["ref"]).write_text("corrupted")
    channel = {"channel_id": "other" if fault == "scope" else "ch",
        "discussions": {"main": {"requirement_message_id": "restart"}},
        "messages": [
            {"message_id": "upload", "thread_id": "main", "role": "user", "text": "原始需求",
             "refs": {"evaluation_source_document": True, "attachments": [attachment, attachment]}},
            {"message_id": "restart", "thread_id": "main", "role": "user", "text": "Continue"},
            {"message_id": "synth", "thread_id": "main", "refs": {"synthesis_request_id": "synth-1"}},
        ]}
    if fault == "trigger":
        channel["messages"][2]["refs"]["attachments"] = [attachment]
        channel["messages"][0]["refs"] = {}
    bundle = build_semantic_source_bundle(channel, thread_id="main", trigger_message_id="synth",
        state_dir=tmp_path, max_source_chars=10 if fault == "budget" else 10000)
    if fault in {"corrupt", "scope", "budget"}:
        assert not bundle["complete"]
        assert bundle["documents"] == []
        assert bundle["reason"]
        return
    assert bundle["complete"]
    if fault != "trigger":
        assert any(d.get("content") == "原始需求" for d in bundle["documents"])
    documents = [d for d in bundle["documents"] if d["kind"] == "channel_attachment"]
    assert len(documents) == 1
    assert documents[0]["content"] == text
    assert attachment["hash"] in bundle["required_message_digests"]
