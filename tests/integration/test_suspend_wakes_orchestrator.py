"""LH-3.T4 hotfix (2026-04-20): verify review.suspended / test.suspended
are in WAKE_PATTERNS so the full end-to-end SUSPEND route works.

Prior history:
- LH-3.T4 added `_on_suspended` handler + event types, and
  `test_hook_recv_defensive.py::test_review_suspended_blocks_task_and_escalates`
  bypasses EventWatcher and pushes events directly into `orchestrator.run_once`.
  That test was green even though the wake_patterns list in `start.py` did
  NOT include `review.suspended` / `test.suspended`, making the real
  end-to-end path silently broken — events would land in events.jsonl
  but EventWatcher never woke the orchestrator.

This test closes that gap.
"""

from __future__ import annotations

from zf.runtime.wake_patterns import WAKE_PATTERNS, reactor_handler_events


def test_review_suspended_is_in_wake_patterns():
    """LH-3.T4: reviewer's SUSPEND must wake Layer 1."""
    assert "review.suspended" in WAKE_PATTERNS, (
        "review.suspended missing from WAKE_PATTERNS — LH-3 SUSPEND "
        "route is broken (events land but orchestrator never wakes "
        "to route them through _on_suspended)."
    )


def test_test_suspended_is_in_wake_patterns():
    """LH-3.T4: tester's SUSPEND must wake Layer 1."""
    assert "test.suspended" in WAKE_PATTERNS, (
        "test.suspended missing from WAKE_PATTERNS — LH-3 SUSPEND "
        "route is broken."
    )


def test_every_reactor_handler_has_wake_pattern():
    """Invariant: an event with a reactor handler but no wake_pattern
    entry is a silent route break (exactly the LH-3 SUSPEND bug class).
    P0-topology backlog surfaces this via `zf validate --cold-start`;
    this test enforces it at commit time."""
    handlers = reactor_handler_events()
    wake = set(WAKE_PATTERNS)
    missing = handlers - wake
    assert not missing, (
        f"Reactor handlers not in WAKE_PATTERNS (silent route break): "
        f"{sorted(missing)}"
    )
