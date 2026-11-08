"""Classify provider TUI screens that still require human input."""

from __future__ import annotations

import re
from typing import Any


_ACTIVE_STATUS = re.compile(
    r"(?m)^\s*[◦•]\s+(?:working|compacting context)\s+\([^\n]*esc to interrupt[^\n]*\)"
)

_INTERACTIVE_PROMPT_MARKERS: tuple[tuple[str, str], ...] = (
    ("login_required", "paste code here if prompted"),
    ("login_required", "oauth error:"),
    ("login_required", "please sign in"),
    ("login_required", "session expired"),
    ("login_required", "please log in"),
    ("trust_prompt", "do you trust the files"),
)


def provider_interactive_prompt_marker(screen: str) -> str:
    """Return the stable blocker class for a provider screen, if any."""

    text = (screen or "").lower()
    active_at = max((m.start() for m in _ACTIVE_STATUS.finditer(text)), default=-1)
    if active_at >= 0:
        text = text[active_at:]
    if "hit your usage limit" in text:
        return "usage_limit_reached"
    if (
        "approaching rate limits" in text
        and "switch to" in text
        and "keep current model" in text
    ):
        return "quota_model_switch"
    if (
        "contains shell syntax (" in text
        and "cannot be statically analyzed" in text
        and "do you want to proceed?" in text
    ):
        return "permission_confirmation"
    for marker, needle in _INTERACTIVE_PROMPT_MARKERS:
        if needle in text:
            return marker
    return ""


def live_provider_interactive_prompt_marker(transport: Any, role_name: str) -> str:
    """Observe a live external gate; never substitute this for worker liveness.

    Watchdogs can defer destructive recovery while the existing Pane Probe
    surfaces the external action. Missing/dead panes retain normal recovery.
    """
    try:
        if not transport.is_alive(role_name):
            return ""
        screen = transport.capture_log(role_name, lines=40)
    except Exception:
        return ""
    return provider_interactive_prompt_marker(str(screen))


__all__ = ["provider_interactive_prompt_marker", "live_provider_interactive_prompt_marker"]
