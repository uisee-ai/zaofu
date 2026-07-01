"""Preflight tests for multi-agent E2E test plan (2026-04-15-2342).

B1: `task.created` must be in wake_patterns so `zf kanban add` actually
    triggers dispatch. Discovered in single-dev smoke test.

B2: `tmux.send_keys(text, enter=True)` must split into two subprocess
    calls so Claude Code's TUI actually submits the prompt. Discovered
    in single-dev smoke test.
"""

from __future__ import annotations

import importlib
import inspect

from zf.runtime.tmux import TmuxSession


class TestB1TaskCreatedInWakePatterns:
    def test_task_created_wake_pattern_present(self):
        from zf.runtime.wake_patterns import WAKE_PATTERNS
        assert "task.created" in WAKE_PATTERNS, (
            "B1 fix: `task.created` must be in wake_patterns so `zf kanban add` "
            "auto-triggers dispatch"
        )


class TestB2SendKeysTextEnterSplit:
    def test_send_keys_emits_two_subprocess_calls_when_enter_true(self):
        tmux = TmuxSession(session_name="t-b2", dry_run=True)
        before = len(tmux.command_log)
        tmux.send_keys("dev-1", "read the briefing", enter=True)
        after_calls = tmux.command_log[before:]
        send_keys_calls = [
            cmd for cmd in after_calls if "send-keys" in cmd
        ]
        assert len(send_keys_calls) == 2, (
            f"B2 fix: send_keys must emit 2 subprocess calls (text + Enter) "
            f"so Claude TUI submits the prompt. Got {len(send_keys_calls)} "
            f"send-keys calls: {send_keys_calls}"
        )

    def test_send_keys_single_call_when_enter_false(self):
        tmux = TmuxSession(session_name="t-b2", dry_run=True)
        before = len(tmux.command_log)
        tmux.send_keys("dev-1", "keystrokes", enter=False)
        after_calls = tmux.command_log[before:]
        send_keys_calls = [
            cmd for cmd in after_calls if "send-keys" in cmd
        ]
        assert len(send_keys_calls) == 1

    def test_send_keys_first_call_is_text_second_is_enter(self):
        tmux = TmuxSession(session_name="t-b2", dry_run=True)
        before = len(tmux.command_log)
        tmux.send_keys("dev-1", "hello world", enter=True)
        after_calls = [c for c in tmux.command_log[before:] if "send-keys" in c]
        assert len(after_calls) == 2
        # First call contains the text, second contains Enter
        assert "hello world" in after_calls[0]
        assert "Enter" in after_calls[1]
        # And the text is NOT in the Enter call (they're separated)
        assert "hello world" not in after_calls[1]
