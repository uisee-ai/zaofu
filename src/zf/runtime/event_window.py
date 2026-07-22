"""Runtime event windows for long-running harness loops.

UI projections can use short "today" windows, but recovery loops need the
current runtime session, including yesterday's archive after lazy rotation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore, ZfNotInitialized


def runtime_event_window_days(
    state_dir: Path,
    *,
    min_days: int = 2,
    max_days: int = 14,
    now: datetime | None = None,
) -> int:
    """Return the archive+active day window needed for this runtime session.

    ``EventLog.read_days(1)`` intentionally means "today only". That is too
    small for recovery once a run crosses UTC midnight and ``events.jsonl`` is
    rotated into ``events/YYYY-MM-DD.jsonl``. Use the session start date when
    available, otherwise keep a two-day fallback that covers the common
    yesterday+today rotation case.
    """

    lower = max(1, int(min_days))
    upper = max(lower, int(max_days))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    try:
        session = SessionStore(Path(state_dir) / "session.yaml").load()
        started_raw = str(session.started_at or "").strip()
        started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        else:
            started = started.astimezone(timezone.utc)
        days = (current.date() - started.date()).days + 1
    except (OSError, TypeError, ValueError, ZfNotInitialized):
        days = lower
    return max(lower, min(upper, days))


def read_runtime_events(
    event_log: EventLog,
    state_dir: Path,
    *,
    min_days: int = 2,
    max_days: int = 14,
) -> list[ZfEvent]:
    """Read the bounded current-session event window for recovery logic."""

    now = datetime.now(timezone.utc)
    days = runtime_event_window_days(
        Path(state_dir),
        min_days=min_days,
        max_days=max_days,
        now=now,
    )
    cutoff = now.date() - timedelta(days=days - 1)
    events = event_log.read_all()
    selected: list[ZfEvent] = []
    for event in events:
        try:
            event_ts = datetime.fromisoformat(
                str(event.ts or "").replace("Z", "+00:00")
            )
            if event_ts.tzinfo is None:
                event_ts = event_ts.replace(tzinfo=timezone.utc)
            else:
                event_ts = event_ts.astimezone(timezone.utc)
        except (TypeError, ValueError):
            # Legacy/malformed records remain visible to recovery rather than
            # being silently discarded because their timestamp is unusable.
            selected.append(event)
            continue
        if event_ts.date() >= cutoff:
            selected.append(event)
    return selected
