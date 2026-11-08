from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner


def test_selected_reads_preserve_order_payload_isolation_and_other_views(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    request = ZfEvent(type="worker.reply.requested", payload={"nested": [{"text": "原文"}]})
    result = ZfEvent(type="worker.reply.sent", causation_id=request.id)
    noise = ZfEvent(type="agent.text", payload={"text": "unrelated"})
    for event in (noise, request, result):
        log.append(event)
    selected = {request.type, result.type}
    for _ in range(2):
        rows = log.read_days(1, event_types=selected)
        assert rows == [request, result]
        rows[0].payload["nested"][0]["text"] = "changed"
        rows[0].type = "changed"
        assert log.read_days(1, event_types={noise.type}) == [noise]
        assert log.read_days(1) == [noise, request, result]
        assert log.read_days(1, event_types=set()) == []


def test_selection_covers_appends_partial_lines_and_rotation(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    request = ZfEvent(type="worker.reply.requested")
    log.append(request)
    wanted = {request.type, "worker.reply.sent"}
    assert log.read_days(1, event_types=wanted) == [request]
    result = ZfEvent(type="worker.reply.sent", causation_id=request.id)
    line = result.to_json()
    with log.path.open("a") as handle:
        handle.write(line[:25])
    assert log.read_days(1, event_types=wanted) == [request]
    with log.path.open("a") as handle:
        handle.write(line[25:] + "\n")
    assert log.read_days(1, event_types=wanted) == [request, result]
    archive_dir = tmp_path / "events"
    archive_dir.mkdir()
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    log.path.rename(archive_dir / f"{day.isoformat()}.jsonl")
    next_request = ZfEvent(type=request.type)
    log.append(next_request)
    assert log.read_days(1, event_types=wanted) == [next_request]
    assert log.read_days(2, event_types=wanted) == [request, result, next_request]


@pytest.mark.parametrize("archived", [False, True])
def test_selection_still_exposes_signed_tampering(tmp_path: Path, archived: bool):
    log = EventLog(tmp_path / "events.jsonl", signer=EventSigner(b"test-key"))
    log.append(ZfEvent(type="agent.text", payload={"text": "original"}))
    path = log.path
    if archived:
        directory = tmp_path / "events"
        directory.mkdir()
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        path = directory / f"{day.isoformat()}.jsonl"
        log.path.rename(path)
    assert log.read_days(2, event_types={"worker.reply.sent"}) == []
    before = path.stat()
    path.write_text(path.read_text().replace("original", "tampered"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert [row.type for row in log.read_days(2, event_types={"worker.reply.sent"})] == [
        "event.malformed",
    ]


def test_selection_applies_to_rotation_during_tail_read(tmp_path: Path, monkeypatch):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="agent.text"))
    assert log.read_days(1, event_types={"worker.reply.sent"}) == []
    log.append(ZfEvent(type="worker.reply.requested"))
    original = EventLog._parse_tail
    result = ZfEvent(type="worker.reply.sent")

    def rotate(self, path, offset, size):
        path.rename(path.with_suffix(".old"))
        path.write_text(ZfEvent(type="agent.text").to_json() + "\n" + result.to_json() + "\n")
        return original(self, path, offset, size)

    monkeypatch.setattr(EventLog, "_parse_tail", rotate)
    assert log.read_days(1, event_types={"worker.reply.sent"}) == [result]
