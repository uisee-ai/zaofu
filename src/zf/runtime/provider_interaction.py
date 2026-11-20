"""Fail closed when a provider's live TUI is awaiting a model choice."""

from zf.runtime.tmux import TmuxError


class ProviderInteractionRequired(TmuxError):
    """An operator must resolve the provider UI before dispatch can resume."""


def has_codex_model_switch_prompt(screen: str) -> bool:
    """Match the active chooser, not the ordinary low-usage footer."""
    lines = [line.strip() for line in screen.splitlines() if line.strip()]
    return bool(
        lines
        and lines[-1] == "enter select · esc back"
        and "Approaching rate limits" in lines
        and any("Switch to " in line and "lower credit usage?" in line for line in lines)
        and any("Keep current model" in line for line in lines)
    )
