"""Bounded idle grace for fanout workers with an open provider turn."""

from __future__ import annotations

from datetime import datetime, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events


def codex_turn_within_idle_grace(
    events: list[ZfEvent], *, instance_id: str, dispatch_epoch: float,
    last_activity_epoch: float, idle_threshold: float, now: float,
) -> bool:
    """An open turn after dispatch extends silence grace, but cannot extend it forever."""
    turn = active_codex_turn_from_events(events, instance_id)
    if turn is None:
        return False
    try:
        started = datetime.fromisoformat(str(turn["started_at"]).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        started_epoch = started.timestamp()
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if not (dispatch_epoch <= started_epoch <= now and dispatch_epoch <= last_activity_epoch <= now):
        return False
    return now - last_activity_epoch < idle_threshold * 4
