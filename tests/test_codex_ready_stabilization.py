"""B-1203-06 R-1: Codex TUI needs a brief stabilization wait after
matching ready_pattern — the `›` prompt prints before stdin is wired,
so the first send_task Enter can be silently dropped.

Fix: BackendAdapter exposes ``post_ready_delay_s``; callers of
wait_ready sleep that long after a successful match. Defaults to 0 so
claude / mock behavior is unchanged.
"""

from __future__ import annotations

import pytest

from zf.runtime.backend import (
    BackendAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    MockAdapter,
)


def test_backend_adapter_exposes_post_ready_delay():
    assert hasattr(BackendAdapter, "post_ready_delay_s"), (
        "BackendAdapter must declare post_ready_delay_s so start.py / "
        "respawn paths can honor per-backend TUI boot quirks"
    )


def test_claude_adapter_has_zero_post_ready_delay():
    """Claude's ❯ prompt appears after stdin is wired — no extra wait."""
    assert ClaudeCodeAdapter().post_ready_delay_s == 0.0


def test_codex_adapter_has_nonzero_post_ready_delay():
    """Codex's › prompt prints during boot; stdin isn't live yet."""
    delay = CodexAdapter().post_ready_delay_s
    assert delay > 0.0, (
        f"codex must insert stabilization delay > 0, got {delay}"
    )
    # Sanity — don't make it absurd
    assert delay <= 5.0, (
        f"post-ready delay must stay reasonable (<5s), got {delay}"
    )


def test_mock_adapter_has_zero_post_ready_delay():
    assert MockAdapter().post_ready_delay_s == 0.0
