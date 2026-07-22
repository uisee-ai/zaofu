"""Tests for events.jsonl active+archive layout (G-EVT-5)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.event_window import read_runtime_events, runtime_event_window_days


def _set_mtime(path: Path, days_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def _day_str(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestEventsActiveArchive:
    def test_append_creates_active_file(self, tmp_path: Path):
        log = EventLog(tmp_path / "events.jsonl")
        log.append(ZfEvent(type="t"))
        assert (tmp_path / "events.jsonl").exists()
        assert not (tmp_path / "events").exists()  # no archive yet

    def test_same_day_appends_do_not_rotate(self, tmp_path: Path):
        log = EventLog(tmp_path / "events.jsonl")
        log.append(ZfEvent(type="a"))
        log.append(ZfEvent(type="b"))
        log.append(ZfEvent(type="c"))
        assert not (tmp_path / "events").exists()
        events = log.read_all()
        assert [e.type for e in events] == ["a", "b", "c"]

    def test_cross_day_append_rotates(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        # Create a legacy events file with mtime from 2 days ago
        path.write_text('{"type":"old","id":"evt-1","ts":"2026-04-12","actor":null,"task_id":null,"payload":{},"causation_id":null,"correlation_id":null}\n')
        _set_mtime(path, days_ago=2)

        log = EventLog(path)
        log.append(ZfEvent(type="new"))

        # Old file rotated to archive
        assert (tmp_path / "events" / f"{_day_str(2)}.jsonl").exists()
        # New active file only has the new event
        active_content = path.read_text()
        assert "new" in active_content
        assert "old" not in active_content

    def test_read_all_merges_active_and_archives(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        archive_dir = tmp_path / "events"
        archive_dir.mkdir()
        # Simulate older archives directly
        (archive_dir / f"{_day_str(3)}.jsonl").write_text(
            '{"type":"a1","id":"e1","ts":"t","actor":null,"task_id":null,"payload":{},"causation_id":null,"correlation_id":null}\n'
        )
        (archive_dir / f"{_day_str(1)}.jsonl").write_text(
            '{"type":"a2","id":"e2","ts":"t","actor":null,"task_id":null,"payload":{},"causation_id":null,"correlation_id":null}\n'
        )
        log = EventLog(path)
        log.append(ZfEvent(type="now"))

        events = log.read_all()
        types = [e.type for e in events]
        # Archived (chronological, oldest first) + today
        assert types == ["a1", "a2", "now"]

    def test_read_days_today_only(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        archive_dir = tmp_path / "events"
        archive_dir.mkdir()
        (archive_dir / f"{_day_str(1)}.jsonl").write_text(
            '{"type":"old","id":"e","ts":"t","actor":null,"task_id":null,"payload":{},"causation_id":null,"correlation_id":null}\n'
        )
        log = EventLog(path)
        log.append(ZfEvent(type="today"))

        events = log.read_days(last_days=1)
        types = [e.type for e in events]
        assert types == ["today"]

    def test_read_days_multi_day(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        archive_dir = tmp_path / "events"
        archive_dir.mkdir()
        for days_ago in (10, 5, 2, 1):
            (archive_dir / f"{_day_str(days_ago)}.jsonl").write_text(
                f'{{"type":"a{days_ago}","id":"e{days_ago}","ts":"t","actor":null,"task_id":null,"payload":{{}},"causation_id":null,"correlation_id":null}}\n'
            )
        log = EventLog(path)
        log.append(ZfEvent(type="now"))

        # last_days=3 = today + 2 days back → day 1, day 2, and today
        events = log.read_days(last_days=3)
        types = [e.type for e in events]
        assert "now" in types
        assert "a1" in types
        assert "a2" in types
        assert "a5" not in types
        assert "a10" not in types

    def test_runtime_event_window_spans_session_start_day(self, tmp_path: Path):
        now = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        started = datetime(2026, 6, 18, 8, tzinfo=timezone.utc)
        (tmp_path / "session.yaml").write_text(
            f"started_at: '{started.isoformat()}'\n",
            encoding="utf-8",
        )

        days = runtime_event_window_days(
            tmp_path,
            min_days=2,
            max_days=14,
            now=now,
        )

        assert days == 4

    def test_runtime_event_window_defaults_to_yesterday_and_today(self, tmp_path: Path):
        assert runtime_event_window_days(tmp_path, min_days=2, max_days=14) == 2

    def test_runtime_event_reads_use_append_fold_and_filter_old_archives(
        self,
        tmp_path: Path,
    ):
        path = tmp_path / "events.jsonl"
        archive_dir = tmp_path / "events"
        archive_dir.mkdir()
        old = ZfEvent(
            type="old",
            ts=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        )
        (archive_dir / f"{_day_str(5)}.jsonl").write_text(
            old.to_json() + "\n",
            encoding="utf-8",
        )
        log = EventLog(path)
        log.append(ZfEvent(type="today"))

        events = read_runtime_events(log, tmp_path, min_days=2, max_days=2)

        assert [event.type for event in events] == ["today"]

    def test_offset_tracking_survives_rotation(self, tmp_path: Path):
        """A4 offset cursor behavior after rotation: when the active file
        shrinks (because of rotation), read_from_offset should detect that
        and return events from position 0 of the new active file."""
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        log.append(ZfEvent(type="pre1"))
        log.append(ZfEvent(type="pre2"))
        offset_before = log.current_offset()

        # Force rotation (simulate cross-day by setting mtime to yesterday)
        _set_mtime(path, days_ago=1)
        # New append triggers rotation
        log.append(ZfEvent(type="post"))

        # Active file now has only "post" — its size is smaller than
        # offset_before. Reading from offset_before should either return
        # nothing (because offset is past EOF) or handle the reset gracefully.
        new_events, new_offset = log.read_from_offset(offset_before)
        # The key invariant: no crash, new_offset points to current file end
        assert new_offset <= log.current_offset()
        # And reading from 0 should return all new-active content
        events_from_zero, _ = log.read_from_offset(0)
        assert any(e.type == "post" for e in events_from_zero)
        assert not any(e.type == "pre1" for e in events_from_zero)

    def test_read_from_offset_leaves_partial_trailing_line_for_next_poll(
        self, tmp_path: Path
    ):
        """A large event (e.g. a 16KB scan report) is flushed to the active file
        in chunks; a 0.5s poll can catch it mid-write, so the file ends with a
        partial, newline-less line. read_from_offset must NOT consume past it —
        decode-fail + advancing the offset to EOF drops that event from the live
        stream forever (a later full re-read still sees it, which is why the
        manifest projector and the live reactor disagree). The partial line must
        be left for the next poll to re-read once the writer finishes.

        Regression: R16 scan-runtime's 16KB refactor.scan.completed was dropped
        this way → wait_for_all scan aggregate hung at 3/3, run dead-stalled."""
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        full_a = log._encode(ZfEvent(type="refactor.scan.completed",
                                     payload={"child_id": "a"}))
        full_b = log._encode(ZfEvent(type="refactor.scan.completed",
                                     payload={"child_id": "b"}))
        split = len(full_b) // 2
        partial_b, rest_b = full_b[:split], full_b[split:]
        # active file: A complete, then B caught mid-write (no trailing newline)
        path.write_text(full_a + "\n" + partial_b, encoding="utf-8")

        events, offset = log.read_from_offset(0)
        # only the complete event is consumed; the offset stops BEFORE the partial
        assert [e.type for e in events] == ["refactor.scan.completed"]
        assert offset == len((full_a + "\n").encode("utf-8"))

        # the writer finishes B (rest of the line + newline)
        with path.open("a", encoding="utf-8") as f:
            f.write(rest_b + "\n")

        events2, _ = log.read_from_offset(offset)
        # the event that was mid-write is delivered on the next poll, not lost
        assert [(e.payload or {}).get("child_id") for e in events2] == ["b"]

    def test_read_all_uses_open_time_snapshot(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        first = log._encode(ZfEvent(type="first"))
        second = log._encode(ZfEvent(type="second"))
        path.write_text(first + "\n", encoding="utf-8")
        original_decode = log._decode
        appended = False

        def decode_and_append(line: str):
            nonlocal appended
            event = original_decode(line)
            if event is not None and event.type == "first" and not appended:
                appended = True
                with path.open("a", encoding="utf-8") as f:
                    f.write(second + "\n")
            return event

        log._decode = decode_and_append  # type: ignore[method-assign]

        assert [event.type for event in log.read_all()] == ["first"]
        assert [event.type for event in log.read_all()] == ["first", "second"]

    def test_rotation_collision_appends(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        archive_dir = tmp_path / "events"
        archive_dir.mkdir()
        yesterday_archive = archive_dir / f"{_day_str(1)}.jsonl"
        yesterday_archive.write_text('{"type":"already"}\n')

        path.write_text('{"type":"yesterday_active"}\n')
        _set_mtime(path, days_ago=1)

        log = EventLog(path)
        log.append(ZfEvent(type="today_fresh"))

        combined = yesterday_archive.read_text()
        assert "already" in combined
        assert "yesterday_active" in combined
        # today_fresh is in the new active file, not archive
        assert path.read_text().count("today_fresh") == 1
