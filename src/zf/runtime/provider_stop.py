"""Provider stop reason taxonomy.

This module normalizes backend-specific stop/error payloads into a small set
of harness-level reasons. The classification is advisory metadata: runtime
truth still flows through canonical task and event transitions.
"""

from __future__ import annotations

from typing import Any
import re


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
    "provider_policy_rejected",
    "transport_error",
})


def classify_provider_stop(payload: dict[str, Any] | None = None, *, status: str = "") -> str:
    data = payload or {}
    error = data.get("error")
    if isinstance(error, dict) and error.get("codex_error_info") == "cyber_policy":
        return "provider_policy_rejected"
    for key in ("provider_stop_reason", "stop_reason", "reason"):
        if isinstance(data.get(key), str) and data[key] in KNOWN_STOP_REASONS:
            return str(data[key])
    if data.get("terminal_event") and not error:
        return "completed_with_terminal_event"
    # A free-form task message is not a provider error. Legacy adapters pass
    # errors in reason/status; retain that error channel until all providers
    # expose typed codes, without searching ordinary successful output.
    failure_context = bool(error) or any(
        marker in str(status or data.get("status") or "").lower()
        for marker in ("error", "failed", "blocked", "timeout")
    )
    text = " ".join(
        str(value)
        for value in (
            status,
            data.get("reason", ""),
            data.get("error", ""),
            data.get("message", "") if failure_context else "",
            data.get("hook_event", ""),
            data.get("hook_event_name", ""),
            data.get("stop_reason", ""),
            data.get("status", ""),
        )
        if value is not None
    ).lower()

    if "rate" in text and "limit" in text:
        return "rate_limited"
    if re.search(r"\b(?:auth|authentication|authorization|unauthorized|forbidden)(?:\b|_)", text):
        return "auth_error"
    if "context" in text and ("limit" in text or "length" in text or "window" in text):
        return "context_limit"
    if "high risk" in text or (
        "policy" in text and ("reject" in text or "block" in text)
    ):
        return "provider_policy_rejected"
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


def provider_stop_action(reason: str) -> str:
    """Map provider-stop facts to the existing deterministic recovery routes."""
    if reason in {
        "auth_error", "hook_review_required", "tool_permission_blocked",
        "provider_policy_rejected",
    }:
        return "suspend"
    if reason == "rate_limited":
        return "cooldown"
    if reason == "context_limit":
        return "recycle"
    if reason in {
        "completed_without_terminal_event", "pending_todos", "timeout", "transport_error",
    }:
        return "requeue"
    return "observe"
