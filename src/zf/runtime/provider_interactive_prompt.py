"""Classify provider TUI screens that still require human input."""

from __future__ import annotations

import re
from typing import Any


_ACTIVE_STATUS = re.compile(
    r"(?m)^\s*[◦•]\s+(?:working|compacting context)\s+\([^\n]*esc to interrupt[^\n]*\)"
)

# Codex resume renders old inline errors alongside later completed turns. This
# boundary only retires that exact historical error, never a login/quota modal.
_CODEX_COMPLETED_FOOTER = re.compile(
    r"(?m)^\s*worked for (?:\d+[hms]\s*)+•\s*\d{1,2}:\d{2}\s*$"
)
_CODEX_REFRESH_ERROR = re.compile(
    r"(?m)^\s*■\s+your\s+access\s+token\s+could\s+not\s+be\s+refreshed\s+because\s+"
    r"you\s+have\s+since\s+logged\s+out\s+or\s+signed\s+in\s+to\s+another\s+account\."
    r"\s+please\s+sign\s+in\s+again\.\s*$"
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
    completed_at = max((m.start() for m in _CODEX_COMPLETED_FOOTER.finditer(text)), default=-1)
    if completed_at >= 0:
        text = _CODEX_REFRESH_ERROR.sub("", text[:completed_at]) + text[completed_at:]
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
