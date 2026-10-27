"""Calendar event windows sharing the Web projection event hydration."""

from __future__ import annotations

from pathlib import Path
from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent


def events_read_days(state_dir: Path, days: int = 1, config: ZfConfig | None = None) -> list[ZfEvent]:
    """EventLog.read_days windows over shared decoded segments.

    Select archive filenames, not event timestamps (same canonical semantics).
    Key includes the UTC date so the calendar window rolls at midnight.
    """
    from datetime import datetime, timezone
    from zf.web.projections import events as shared

    from zf.core.events.factory import event_log_from_project

    day_key = datetime.now(timezone.utc).date().isoformat()

    def build():
        from zf.core.state.rotation import list_archives

        rows = shared._events_with_seq(state_dir, config=config)
        entry = shared._EVENTS_WITH_SEQ_CACHE.get(str(state_dir))
        if entry is None or entry["events"] is not rows:
            # A concurrent project may have evicted this cache entry.
            return list(event_log_from_project(state_dir, config=config).read_days(days))
        allowed = {"events.jsonl"}
        if days > 1:
            allowed.update(
                path.relative_to(state_dir).as_posix()
                for path in list_archives(state_dir / "events", last_days=days - 1, suffix=".jsonl")
            )
        return [
            event
            for segment, start, end in entry["segment_ranges"]
            if segment in allowed
            for _, event in rows[start:end]
            if not (event.type == "event.malformed" and event.actor == "segments")
        ]

    def fold(old: list, tail) -> list:
        # New rows are active-file appends = inside every read_days window.
        # _parse_file silently skips non-JSON lines; the events cache injects
        # an event.malformed placeholder (actor=segments) for them — drop
        # those to keep exact parity. Schema-invalid JSON (actor=zf-cli) stays.
        fresh = [
            event
            for _seq, event in tail
            if not (
                getattr(event, "type", "") == "event.malformed"
                and getattr(event, "actor", "") == "segments"
            )
        ]
        return old + fresh if fresh else old

    return shared._derived_cached(state_dir, f"read_days:{days}:{day_key}", config, build, fold=fold)
