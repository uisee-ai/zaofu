"""Provider turn lifecycle reconstruction for deterministic liveness fences."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.event_window import read_runtime_events


def active_codex_turn(
    event_log: EventLog,
    state_dir: Path,
    instance_id: str,
) -> dict[str, object] | None:
    """Return the newest open Codex turn for a worker, if any."""

    try:
        events = read_runtime_events(event_log, state_dir)
    except Exception:
        return None

    return active_codex_turn_from_events(events, instance_id)


def active_codex_turn_from_events(
    events: Iterable[ZfEvent], instance_id: str,
) -> dict[str, object] | None:
    """Reconstruct an open turn from an already loaded event window."""

    active: dict[tuple[str, str], ZfEvent] = {}
    recycling_turns: set[tuple[str, str]] | None = None
    for event in events:
        if event.type == "loop.stopped":
            active.clear()
            recycling_turns = None
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "worker.launch_artifact.written"
            and str(payload.get("instance_id") or "") == instance_id
        ):
            active.clear()
            recycling_turns = None
            continue
        if (
            event.type == "role.lifecycle.dormant"
            and str(payload.get("instance_id") or "") == instance_id
        ):
            active.clear()
            recycling_turns = None
            continue
        if event.actor != instance_id:
            continue
        if event.type not in {
            "codex.hook.user_prompt_submit",
            "codex.hook.stop",
            "provider.turn.closed",
            "worker.respawned",
            "worker.recycling",
            "worker.recycled",
        }:
            continue
        if event.type == "worker.respawned":
            active.clear()
            recycling_turns = None
            continue
        if event.type == "worker.recycling":
            recycling_turns = set(active)
            continue
        if event.type == "worker.recycled":
            if recycling_turns is None:
                active.clear()
            else:
                for key in recycling_turns:
                    active.pop(key, None)
            recycling_turns = None
            continue
        session_id = str(payload.get("session_id") or "").strip()
        turn_id = str(payload.get("turn_id") or "").strip()
        if event.type == "provider.turn.closed":
            if str(payload.get("backend") or "") != "codex" or not turn_id:
                continue
            for key in list(active):
                if key[1] == turn_id:
                    active.pop(key, None)
            continue
        if not session_id:
            continue
        if event.type == "codex.hook.user_prompt_submit":
            if turn_id:
                active[(session_id, turn_id)] = event
            continue
        if turn_id:
            active.pop((session_id, turn_id), None)
        else:
            for key in list(active):
                if key[0] == session_id:
                    active.pop(key, None)

    if not active:
        return None

    latest_key, latest_event = max(
        active.items(),
        key=lambda item: _parse_event_ts(item[1].ts)
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    started_at = _parse_event_ts(latest_event.ts)
    age_s = None
    if started_at is not None:
        age_s = (datetime.now(timezone.utc) - started_at).total_seconds()
    return {
        "session_id": latest_key[0],
        "turn_id": latest_key[1],
        "started_at": latest_event.ts,
        "age_s": age_s,
    }


def _parse_event_ts(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = ["active_codex_turn", "active_codex_turn_from_events"]
