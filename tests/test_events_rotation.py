"""Immutable event segments keep durable consumers correct across collisions."""

from datetime import datetime, timedelta, timezone
import os

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.offset import EventOffset
from zf.core.state.session import SessionStore


def _yesterday(path):
    timestamp = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_repeated_day_collision_keeps_unread_tail_and_snapshot_after_restart(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    _yesterday(log.path)
    log.append(ZfEvent(type="second"))
    start = log.current_offset()
    session = SessionStore(tmp_path / "session.yaml")
    session.create(str(tmp_path))
    session.update(latest_event_offset=start)
    log.append(ZfEvent(type="third"))
    boundary = log.current_offset()
    _yesterday(log.path)
    log.append(ZfEvent(type="fourth"))
    _yesterday(log.path)
    log.append(ZfEvent(type="fifth"))

    loaded = SessionStore(session.path).load()
    restored = EventOffset(loaded.latest_event_offset, tuple(loaded.latest_event_identity))
    restarted = EventLog(log.path)
    events, consumed = restarted.read_from_offset(restored, through=boundary)
    assert [event.type for event in events] == ["third"]
    assert consumed.identity == start.identity
    tail, end = restarted.read_from_offset(consumed)
    assert [event.type for event in tail] == ["fourth", "fifth"]
    assert restarted.read_from_offset(end)[0] == []
    assert [event.type for event in restarted.read_all()] == [
        "first", "second", "third", "fourth", "fifth",
    ]


def test_size_rotation_after_existing_final_segment_keeps_occurrence_order(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    archive = tmp_path / "events"
    archive.mkdir()
    day = datetime.now(timezone.utc).date().isoformat()
    log.path.rename(archive / f"{day}.jsonl")
    log.append(ZfEvent(type="second"))
    start = log.current_offset()
    monkeypatch.setenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", "1")
    log.append(ZfEvent(type="third"))
    log.append(ZfEvent(type="fourth"))
    assert [event.type for event in log.read_all()] == ["first", "second", "third", "fourth"]
    assert [event.type for event in log.read_from_offset(start)[0]] == ["third", "fourth"]


def test_missing_bound_history_fails_without_silently_skipping_events(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    start = log.current_offset()
    # Hold the inode outside the segment inventory to avoid inode reuse.
    log.path.rename(tmp_path / "lost-ledger")
    log.append(ZfEvent(type="second"))
    with pytest.raises(ValueError, match="cursor segment is no longer available"):
        log.read_from_offset(start)
    with pytest.raises(ValueError, match="consumed event segment is no longer available"):
        log.read_from_offset(0, through=start)


def test_legacy_session_cursor_binds_current_file_on_first_read(tmp_path):
    session = SessionStore(tmp_path / "session.yaml")
    session.path.write_text("latest_event_offset: 0\n", encoding="utf-8")
    loaded = session.load()
    assert loaded.latest_event_identity == []
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    events, cursor = log.read_from_offset(loaded.latest_event_offset)
    session.update(latest_event_offset=cursor)
    assert [event.type for event in events] == ["first"]
    assert session.load().latest_event_identity == list(cursor.identity)
