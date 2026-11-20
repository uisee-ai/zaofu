"""Rotation-aware event positions preserve exact, independent read boundaries."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os

import pytest

from zf.core.events.log import EventLog
from zf.core.events.offset import EventOffset
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner
from zf.core.state.session import SessionStore


@pytest.mark.parametrize("rotation", ["day", "size"])
@pytest.mark.parametrize("signed", [False, True])
def test_bound_offset_follows_real_rotation_and_unread_tail(tmp_path, monkeypatch, rotation, signed):
    signer = EventSigner(b"test-key") if signed else None
    log = EventLog(tmp_path / "events.jsonl", signer=signer)
    old = ZfEvent(type="worker.heartbeat", id="evt-old")
    log.append(old)
    offset = log.current_offset()
    unread = replace(old, id="evt-tail")
    log.append(unread)
    if rotation == "size":
        monkeypatch.setenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", str(log.path.stat().st_size))
    else:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
        os.utime(log.path, (yesterday, yesterday))
    fresh = replace(old, id="evt-new", payload={"padding": "x" * 2000})
    log.append(fresh)
    events, consumed = EventLog(log.path, signer=signer).read_from_offset(offset)
    assert [e.id for e in events] == [unread.id, fresh.id]
    assert consumed.identity != offset.identity
    assert log.read_from_offset(consumed)[0] == []


def test_bounded_read_does_not_consume_callback_append_or_move_backwards(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    start = log.current_offset()
    log.append(ZfEvent(type="second"))
    boundary = log.current_offset()
    log.append(ZfEvent(type="third"))
    events, consumed = log.read_from_offset(start, through=boundary)
    assert [e.type for e in events] == ["second"]
    assert consumed == boundary
    events, end = log.read_from_offset(consumed)
    assert [e.type for e in events] == ["third"]
    assert log.read_from_offset(end, through=boundary) == ([], end)
    assert [e.type for e in log.read_from_offset(start)[0]] == ["second", "third"]


def test_bounded_read_still_targets_archived_batch(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    start = log.current_offset()
    log.append(ZfEvent(type="second"))
    boundary = log.current_offset()
    archive = tmp_path / "events"
    archive.mkdir()
    log.path.rename(archive / "2026-10-30.jsonl")
    log.append(ZfEvent(type="third"))
    events, consumed = log.read_from_offset(start, through=boundary)
    assert [e.type for e in events] == ["second"]
    assert consumed.identity == boundary.identity
    assert [e.type for e in log.read_from_offset(consumed)[0]] == ["third"]


def test_session_offset_identity_survives_safe_yaml_roundtrip(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    offset = log.current_offset()
    store = SessionStore(tmp_path / "session.yaml")
    store.create(str(tmp_path))
    store.update(latest_event_offset=offset)
    loaded = store.load()
    restored = EventOffset(loaded.latest_event_offset, tuple(loaded.latest_event_identity))
    assert restored == offset and restored.identity == offset.identity
    store.update(latest_event_offset=0)
    assert store.load().latest_event_identity == []
