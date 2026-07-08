from __future__ import annotations

from zf.cli.goal import _SETTABLE_STATUSES


def test_goal_cli_accepts_limit_statuses() -> None:
    assert "usage_limited" in _SETTABLE_STATUSES
    assert "budget_limited" in _SETTABLE_STATUSES
