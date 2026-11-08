"""A live reused provider must not consume task input in a model chooser."""

from pathlib import Path

import pytest

from zf.runtime.provider_interactive_prompt import provider_interactive_prompt_marker
from zf.runtime.tmux import TmuxError
from zf.runtime.transport import DispatchContext, TmuxTransport


CHOOSER = """Approaching rate limits
Switch to gpt-6-luna for lower usage?
› 1. Switch to gpt-6-luna
  2. Keep current model
enter select · esc back
"""
BANNER = "Heads up, you have less than 5% of your weekly limit left. Run /status."


class Pane:
    dry_run = False

    def __init__(self, screen: str):
        self.screen = screen
        self.inputs = []

    def pane_pid(self, role):
        return "42"

    def pane_alive(self, role):
        return True

    def pane_current_command(self, role):
        return "codex"

    def pane_current_path(self, role):
        return "/project"

    def capture_pane(self, role, *, lines=120):
        return self.screen

    def wait_for_prompt(self, role, pattern, *, timeout):
        return True

    def clear_input(self, role):
        self.inputs.append("clear")

    def send_paste(self, role, prompt):
        self.inputs.append(prompt)


def test_quota_model_chooser_is_not_a_passive_warning():
    assert provider_interactive_prompt_marker(CHOOSER) == "quota_model_switch"
    assert provider_interactive_prompt_marker(BANNER) == ""


def test_hard_quota_failure_takes_precedence_over_model_chooser():
    screen = "■ You’ve hit your usage limit. Try again later.\n" + CHOOSER
    assert provider_interactive_prompt_marker(screen) == "usage_limit_reached"
    transport = TmuxTransport(Pane(screen))
    assert not transport.wait_ready("planner", r"[›>]", timeout=1)
    assert transport.readiness_diagnostics("planner")["failure_class"] == "provider_quota_interactive"


def test_old_quota_error_does_not_mask_a_resumed_active_turn():
    screen = "■ You’ve hit your usage limit.\n◦ Working (1m • esc to interrupt)"
    assert provider_interactive_prompt_marker(screen) == ""


@pytest.mark.parametrize("old_prompt", [CHOOSER, "Please sign in", "Do you trust the files?"])
def test_historical_prompts_do_not_park_a_resumed_worker(old_prompt):
    screen = old_prompt + "\n◦ Working (2m • esc to interrupt)"
    assert provider_interactive_prompt_marker(screen) == ""


def test_reused_provider_checks_current_modal_before_touching_input():
    pane = Pane(BANNER)
    transport = TmuxTransport(pane)
    assert transport.wait_ready("planner", r"[›>]", timeout=1)
    # A quota chooser appears after initial readiness, before the next dispatch.
    pane.screen = CHOOSER
    with pytest.raises(TmuxError):
        transport.send_task("planner", Path("briefing.md"), "new task",
                            context=DispatchContext(backend="codex"))
    assert pane.inputs == []
    assert transport.readiness_diagnostics("planner")["interactive_prompt_marker"] == "quota_model_switch"

    # Operator dismissed the dialog; ordinary low-quota warning does not block.
    pane.screen = BANNER
    transport.send_task("planner", Path("briefing.md"), "new task",
                        context=DispatchContext(backend="codex"))
    assert pane.inputs == ["clear", "new task"]


def test_startup_readiness_rejects_quota_model_chooser():
    transport = TmuxTransport(Pane(CHOOSER))
    assert not transport.wait_ready("planner", r"[›>]", timeout=1)


@pytest.mark.parametrize("screen", ["Please sign in", "Do you trust the files?"])
def test_existing_interactive_blockers_also_prevent_dispatch(screen):
    pane = Pane(screen)
    with pytest.raises(TmuxError):
        TmuxTransport(pane).send_task("planner", Path("briefing.md"), "new task",
                                      context=DispatchContext(backend="codex"))
    assert pane.inputs == []
