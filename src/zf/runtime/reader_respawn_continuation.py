"""Mechanical fences for an in-flight reader's controlled provider replacement."""

import fcntl
from pathlib import Path

from zf.core.events.model import ZfEvent


def reader_respawn_in_progress(state_dir: Path, role_instance: str) -> bool:
    """Observe the existing replacement lock without creating or unlinking it."""
    path = state_dir / "locks" / "respawns" / f"{role_instance}.lock"
    try:
        handle = path.open("rb")
    except OSError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def reader_recovery_fence(
    events: list[ZfEvent], dispatch: ZfEvent, dispatch_index: int
) -> int:
    """Fence only signals preceding delivery for this exact original dispatch."""
    original = dispatch.payload
    role = str(original.get("role_instance") or "")
    expected = {
        "recovered_dispatch_event_id": dispatch.id,
        "fanout_id": str(original.get("fanout_id") or ""),
        "run_id": str(original.get("run_id") or ""),
    }
    if not role or not all(expected.values()):
        return dispatch_index
    for key in ("operation_id", "request_hash"):
        if original.get(key):
            expected[key] = str(original[key])
    for index in range(len(events) - 1, dispatch_index, -1):
        event = events[index]
        if event.type != "worker.recovery.injected" or event.actor != role:
            continue
        if all(
            str(event.payload.get(k) or "") == value for k, value in expected.items()
        ):
            return index
    return dispatch_index
