"""Classify provider TUI screens that still require human input."""

from __future__ import annotations


_INTERACTIVE_PROMPT_MARKERS: tuple[tuple[str, str], ...] = (
    ("usage_limit_reached", "hit your usage limit"),
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
    if "quick safety check:" in text and "yes, i trust this folder" in text:
        return "trust_prompt"
    if "security notes:" in text and "press enter to continue" in text:
        return "onboarding_prompt"
    if (
        "contains shell syntax (" in text
        and "cannot be statically analyzed" in text
        and "do you want to proceed?" in text
    ):
        return "permission_confirmation"
    if "this command requires approval" in text and "do you want to proceed?" in text:
        return "permission_confirmation"
    for marker, needle in _INTERACTIVE_PROMPT_MARKERS:
        if needle in text:
            return marker
    return ""


__all__ = ["provider_interactive_prompt_marker"]
