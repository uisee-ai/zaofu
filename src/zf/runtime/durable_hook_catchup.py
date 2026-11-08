"""Lightweight durable catch-up for suppressed Codex tool hooks."""

from __future__ import annotations

from typing import Any

from zf.runtime.wake_patterns import wake_worthy


_SUPPRESSED_TOOL_HOOKS = frozenset({
    "codex.hook.pre_tool_use",
    "codex.hook.post_tool_use",
})


def consume_durable_nonwake_hooks(runtime: Any) -> bool:
    """Apply Layer 1 liveness for a pure suppressed Hook batch.

    The watcher deliberately leaves high-rate allow/post-tool Hook events for
    durable catch-up. They are occurrence and liveness facts, not a reason to
    run replay, recovery, dispatch, or Layer 2 for every batch. Defer at most
    one maintenance cycle: sustained hooks must not starve periodic recovery.
    Persist only the immutable
    boundary returned by ``read_from_offset``; callback appends remain beyond
    that cursor for the next cycle.
    """

    deferred = getattr(runtime, "_nonwake_maintenance_deferred", False)
    runtime._nonwake_maintenance_deferred = False
    offset = runtime._load_offset()
    recent, new_offset = runtime.event_log.read_from_offset(offset)
    if not recent:
        return False
    if any(
        event.type not in _SUPPRESSED_TOOL_HOOKS or wake_worthy(event)
        for event in recent
    ):
        return False
    for event in recent:
        if event.id in runtime._processed_event_ids:
            continue
        if event.id not in runtime._transport_housekept_event_ids:
            runtime._apply_housekeeping(event)
        runtime._processed_event_ids.add(event.id)
    runtime._persist_offset(new_offset)
    if runtime._load_offset() != new_offset or deferred:
        return False
    runtime._nonwake_maintenance_deferred = True
    return True


__all__ = ["consume_durable_nonwake_hooks"]
