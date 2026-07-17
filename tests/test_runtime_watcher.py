"""Tests for event watcher — file polling + stuck detection."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner
from zf.runtime.watcher import EventWatcher, StuckDetector


class TestEventWatcher:
    def test_detects_new_event(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")

        detected: list[str] = []
        watcher = EventWatcher(events_file, on_event=lambda line: detected.append(line))

        # Append a new event
        with events_file.open("a") as f:
            f.write(json.dumps({"type": "test.event"}) + "\n")

        # Poll once
        watcher.poll_once()
        assert len(detected) == 1
        assert "test.event" in detected[0]

    def test_skips_already_read_lines(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(json.dumps({"type": "old"}) + "\n")

        detected: list[str] = []
        watcher = EventWatcher(events_file, on_event=lambda line: detected.append(line))

        # First poll reads existing content
        watcher.poll_once()
        # old event is NOT detected (watcher starts from end of file)
        assert len(detected) == 0

        # Now add new event
        with events_file.open("a") as f:
            f.write(json.dumps({"type": "new"}) + "\n")

        watcher.poll_once()
        assert len(detected) == 1
        assert "new" in detected[0]

    def test_handles_multiple_new_lines(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")

        detected: list[str] = []
        watcher = EventWatcher(events_file, on_event=lambda line: detected.append(line))

        with events_file.open("a") as f:
            f.write(json.dumps({"type": "a"}) + "\n")
            f.write(json.dumps({"type": "b"}) + "\n")
            f.write(json.dumps({"type": "c"}) + "\n")

        watcher.poll_once()
        assert len(detected) == 3

    def test_handles_missing_file_gracefully(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        # File does not exist
        detected: list[str] = []
        watcher = EventWatcher(events_file, on_event=lambda line: detected.append(line))
        # Should not crash
        watcher.poll_once()
        assert len(detected) == 0

    def test_skips_empty_lines(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")

        detected: list[str] = []
        watcher = EventWatcher(events_file, on_event=lambda line: detected.append(line))

        with events_file.open("a") as f:
            f.write("\n")
            f.write(json.dumps({"type": "real"}) + "\n")
            f.write("\n")

        watcher.poll_once()
        assert len(detected) == 1

    def test_is_wake_event_matches_configured_patterns(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")

        watcher = EventWatcher(
            events_file,
            on_event=lambda line: None,
            wake_patterns=["dev.build.done", "review.rejected"],
        )
        assert watcher.is_wake_event('{"type": "dev.build.done"}')
        assert watcher.is_wake_event('{"type": "review.rejected"}')
        assert not watcher.is_wake_event('{"type": "gate.passed"}')

    def test_detects_signed_envelope(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        signer = EventSigner(b"secret")
        log = EventLog(events_file, signer=signer)
        detected: list[str] = []
        watcher = EventWatcher(
            events_file,
            on_event=lambda line: detected.append(line),
            event_log=EventLog(events_file, signer=signer),
        )

        log.append(ZfEvent(type="dev.build.done", actor="dev"))
        watcher.poll_once()

        assert len(detected) == 1
        assert json.loads(detected[0])["type"] == "dev.build.done"

    def test_detects_new_active_file_after_rotation(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        log = EventLog(events_file)
        for idx in range(10):
            log.append(ZfEvent(
                type=f"pre.rotate.{idx}",
                payload={"padding": "x" * 500},
            ))
        detected: list[str] = []
        watcher = EventWatcher(
            events_file,
            on_event=lambda line: detected.append(line),
            event_log=log,
        )

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
        os.utime(events_file, (yesterday, yesterday))
        log.append(ZfEvent(type="post.rotate", actor="arch", task_id="TASK-1"))

        watcher.poll_once()

        assert [json.loads(line)["type"] for line in detected] == ["post.rotate"]

    def test_stop_flag(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        watcher = EventWatcher(events_file, on_event=lambda line: None)
        assert not watcher.stopped
        watcher.stop()
        assert watcher.stopped

    def test_run_invokes_periodic_tick_without_new_events(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")

        ticks: list[float] = []

        def on_tick() -> None:
            ticks.append(time.monotonic())
            watcher.stop()

        watcher = EventWatcher(
            events_file,
            on_event=lambda line: None,
            on_tick=on_tick,
        )

        watcher.run(poll_interval=0.001, tick_interval=0.001)

        assert ticks


class TestStuckDetector:
    def test_not_stuck_initially(self):
        detector = StuckDetector(stale_threshold=0.1)
        assert not detector.is_stuck()

    def test_not_stuck_when_output_changes(self):
        detector = StuckDetector(stale_threshold=0.1)
        detector.update("output 1")
        detector.update("output 2")
        assert not detector.is_stuck()

    def test_stuck_when_same_output_exceeds_threshold(self):
        detector = StuckDetector(stale_threshold=0.05)
        detector.update("same output")
        time.sleep(0.06)
        detector.update("same output")
        assert detector.is_stuck()

    def test_not_stuck_after_output_changes(self):
        detector = StuckDetector(stale_threshold=0.05)
        detector.update("same output")
        time.sleep(0.06)
        detector.update("same output")
        assert detector.is_stuck()
        # Now output changes
        detector.update("different output")
        assert not detector.is_stuck()

    def test_reset(self):
        detector = StuckDetector(stale_threshold=0.05)
        detector.update("output")
        time.sleep(0.06)
        detector.update("output")
        assert detector.is_stuck()
        detector.reset()
        assert not detector.is_stuck()


class TestShutdownMarker:
    """ZF-STOP-TAIL-01: watcher 每轮先看停机标记,1 个 poll 周期内自退。"""

    def test_run_exits_when_marker_exists(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        marker = tmp_path / "shutdown-requested"
        marker.write_text("")
        polled: list[int] = []
        watcher = EventWatcher(
            events_file,
            on_event=lambda line: None,
            shutdown_marker=marker,
        )
        watcher.poll_once = lambda: polled.append(1)  # type: ignore[method-assign]
        watcher.run(poll_interval=0.01)
        assert watcher.stopped is True
        assert polled == []  # 标记先于任何 poll 被看见

    def test_run_keeps_polling_without_marker(self, tmp_path: Path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("")
        marker = tmp_path / "shutdown-requested"  # 不存在
        watcher = EventWatcher(
            events_file,
            on_event=lambda line: None,
            shutdown_marker=marker,
        )
        calls: list[int] = []

        def _poll() -> None:
            calls.append(1)
            if len(calls) >= 3:
                marker.write_text("")  # 第 3 轮后写标记 → 下一轮自退

        watcher.poll_once = _poll  # type: ignore[method-assign]
        watcher.run(poll_interval=0.01)
        assert watcher.stopped is True
        assert len(calls) == 3
