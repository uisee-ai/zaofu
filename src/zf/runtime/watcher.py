"""Event watcher — poll events.jsonl for new lines + stuck detection."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable

from zf.core.events.log import EventLog


# doc 80 rev1 § 7 P4 — quiesce defaults.
# R14 真跑:safe-halt 后 watcher 整夜空转 878 条 dispatch_skipped。fix:
# watcher 监听 safe_halted / paused → 进入 quiesce 态(polling 间隔放大,
# 不停止 read events,否则在 quiesce 后看不到 runtime.resumed 会死锁)。
DEFAULT_QUIESCE_PATTERNS: tuple[str, ...] = (
    "runtime.safe_halted",
    "dispatch.paused",
)
DEFAULT_RESUME_PATTERNS: tuple[str, ...] = (
    "runtime.resumed",
    "dispatch.resumed",
)
DEFAULT_QUIESCE_FACTOR: float = 10.0


class EventWatcher:
    """Watch events.jsonl for new lines via file position polling."""

    def __init__(
        self,
        events_path: Path,
        on_event: Callable[[str], None],
        *,
        on_tick: Callable[[], None] | None = None,
        wake_patterns: list[str] | None = None,
        event_log: EventLog | None = None,
        quiesce_patterns: list[str] | None = None,
        resume_patterns: list[str] | None = None,
        quiesce_factor: float = DEFAULT_QUIESCE_FACTOR,
        shutdown_marker: Path | None = None,
    ) -> None:
        self.events_path = events_path
        self.on_event = on_event
        self.on_tick = on_tick
        self.wake_patterns = wake_patterns or []
        self.event_log = event_log or EventLog(events_path)
        self.shutdown_marker = shutdown_marker
        self.stopped = False
        # Start from end of file (don't replay old events)
        self._file_pos = self._get_file_size()
        # P4 quiesce state — events still consumed, just at amplified
        # poll interval so safe-halt doesn't broadcast dispatch_skipped noise.
        self.quiesce_patterns = (
            list(quiesce_patterns) if quiesce_patterns is not None
            else list(DEFAULT_QUIESCE_PATTERNS)
        )
        self.resume_patterns = (
            list(resume_patterns) if resume_patterns is not None
            else list(DEFAULT_RESUME_PATTERNS)
        )
        self.quiesce_factor = quiesce_factor
        self._quiesced = False

    def _get_file_size(self) -> int:
        try:
            return self.events_path.stat().st_size
        except FileNotFoundError:
            return 0

    def poll_once(self) -> list[str]:
        """Read any new lines since last poll. Returns new lines."""
        new_lines: list[str] = []
        events, new_offset = self.event_log.read_from_offset(self._file_pos)
        self._file_pos = new_offset
        for event in events:
            event_json = event.to_json()
            new_lines.append(event_json)
            self.on_event(event_json)
            # P4: a single event may flip quiesce on or off; within a single
            # batch the last matching pattern wins (resume after paused →
            # active, paused after resume → quiesced).
            etype = getattr(event, "type", "") or ""
            if etype in self.quiesce_patterns:
                self._quiesced = True
            if etype in self.resume_patterns:
                self._quiesced = False
        return new_lines

    # P4 quiesce introspection ------------------------------------------------

    def is_quiesced(self) -> bool:
        return self._quiesced

    def effective_poll_interval(self, base: float) -> float:
        """The poll interval the `run` loop will use this iteration. Exposed
        for tests so they can assert the amplification without sleeping."""
        return base * self.quiesce_factor if self._quiesced else base

    def is_wake_event(self, line: str) -> bool:
        """Check if a line contains a wake-triggering event type."""
        event = self.event_log.decode_line(line)
        if event is not None:
            return event.type in self.wake_patterns
        for pattern in self.wake_patterns:
            if pattern in line:
                return True
        return False

    def stop(self) -> None:
        self.stopped = True

    def run(
        self,
        *,
        poll_interval: float = 0.5,
        tick_interval: float = 0.0,
    ) -> None:
        """Run the polling loop until stopped."""
        next_tick = (
            time.monotonic() + tick_interval
            if self.on_tick is not None and tick_interval > 0
            else 0.0
        )
        while not self.stopped:
            # ZF-STOP-TAIL-01: `zf stop` 的信号杀按 loop.lock pid,与真实
            # watcher pid 错位时打空,watcher 曾拖 5-6 分钟排空积压并对
            # 已杀 pane 打出 stall/stuck/RM 立案风暴。标记文件是停机序列
            # 第一步写入的,这里每轮先看它,1 个 poll 周期内自退。
            if self.shutdown_marker is not None and self.shutdown_marker.exists():
                self.stopped = True
                break
            self.poll_once()
            if self.on_tick is not None and tick_interval > 0:
                now = time.monotonic()
                if now >= next_tick:
                    self.on_tick()
                    next_tick = now + tick_interval
            time.sleep(self.effective_poll_interval(poll_interval))


class StuckDetector:
    """Detect when pane output hasn't changed for a configurable duration."""

    def __init__(self, stale_threshold: float = 300.0) -> None:
        self.stale_threshold = stale_threshold
        self._last_hash: str | None = None
        self._last_change_time: float = time.monotonic()

    def update(self, output: str) -> None:
        """Update with new pane output."""
        current_hash = hashlib.md5(output.encode()).hexdigest()
        if current_hash != self._last_hash:
            self._last_hash = current_hash
            self._last_change_time = time.monotonic()

    def is_stuck(self) -> bool:
        """Return True if output has been unchanged longer than threshold."""
        if self._last_hash is None:
            return False
        elapsed = time.monotonic() - self._last_change_time
        return elapsed > self.stale_threshold

    def reset(self) -> None:
        """Reset the detector state."""
        self._last_hash = None
        self._last_change_time = time.monotonic()
