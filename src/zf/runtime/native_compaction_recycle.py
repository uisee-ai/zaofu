"""Bound forced recycling while a Codex native compact is visibly in flight."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from zf.core.events.model import ZfEvent

if TYPE_CHECKING:
    from zf.core.config.schema import RoleConfig


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_STATUS = re.compile(r"^\s*[◦•]\s+(.+?)\s+\([^\n]*esc to interrupt[^\n]*\)")


def defer_native_compaction_recycle(
    runtime: Any,
    role: RoleConfig,
    *,
    session_id: str,
    hard_cap_since: float,
) -> bool:
    """Allow one drain-hold interval, without declaring compaction successful.

    The timer belongs to this session and hard-cap episode. It is not refreshed
    by repeated pane observations. Keep the short grace after the status clears:
    the last usage sample may still describe the pre-compaction context.
    """
    if role.backend != "codex" or not session_id:
        return False
    entries = getattr(runtime, "_native_compaction_recycle_grace", None)
    if entries is None:
        entries = {}
        runtime._native_compaction_recycle_grace = entries
    episode = (session_id, hard_cap_since)
    now = runtime._now()
    previous = entries.get(role.instance_id)
    if previous is not None and previous[0] == episode:
        return now < previous[1]
    try:
        pane = runtime.transport.capture_log(role.instance_id, lines=40)
    except Exception:
        return False
    statuses = [
        match.group(1)
        for line in _ANSI.sub("", pane).splitlines()
        if (match := _STATUS.match(line))
    ]
    if not statuses or statuses[-1] != "Compacting context":
        return False
    deadline = now + role.drain_hold_seconds
    entries[role.instance_id] = (episode, deadline)
    runtime.event_writer.append(ZfEvent(
        type="worker.context.warning",
        actor=role.instance_id,
        payload={
            "reason": "native_compaction_recycle_grace",
            "session_id": session_id,
            "hard_cap_since_epoch": hard_cap_since,
            "defer_until_epoch": deadline,
            "observation": "Compacting context",
            "strategy": "bounded_wait_preserve_current_attempt",
        },
    ))
    return now < deadline
