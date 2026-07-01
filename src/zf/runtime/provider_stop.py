"""Provider stop reason taxonomy.

This module normalizes backend-specific stop/error payloads into a small set
of harness-level reasons. The classification is advisory metadata: runtime
truth still flows through canonical task and event transitions.
"""

from __future__ import annotations

from typing import Any


KNOWN_STOP_REASONS = frozenset({
    "completed_with_terminal_event",
    "completed_without_terminal_event",
    "pending_todos",
    "context_limit",
    "rate_limited",
    "auth_error",
    "tool_permission_blocked",
    "hook_review_required",
    "timeout",
    "manual_interrupt",
    "transport_error",
})


def classify_provider_stop(payload: dict[str, Any] | None = None, *, status: str = "") -> str:
    data = payload or {}
    text = " ".join(
        str(value)
        for value in (
            status,
            data.get("reason", ""),
            data.get("error", ""),
            data.get("message", ""),
            data.get("hook_event", ""),
            data.get("hook_event_name", ""),
            data.get("stop_reason", ""),
            data.get("status", ""),
        )
        if value is not None
    ).lower()

    if "rate" in text and "limit" in text:
        return "rate_limited"
    if "auth" in text or "unauthorized" in text or "forbidden" in text:
        return "auth_error"
    if "context" in text and ("limit" in text or "length" in text or "window" in text):
        return "context_limit"
    if "permission" in text or "deny" in text or "blocked" in text:
        return "tool_permission_blocked"
    if "hook" in text and ("review" in text or "approve" in text or "trust" in text):
        return "hook_review_required"
    if "todo" in text or "pending" in text or "incomplete" in text:
        return "pending_todos"
    if "timeout" in text or status == "timeout":
        return "timeout"
    if "interrupt" in text or "cancel" in text:
        return "manual_interrupt"
    if "transport" in text or "parse" in text or "connection" in text:
        return "transport_error"
    if data.get("terminal_event"):
        return "completed_with_terminal_event"
    return "completed_without_terminal_event"
