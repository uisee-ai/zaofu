"""1206 Phase A — TmuxLayout abstraction + PaneTarget + two strategies.

Phase A scope:
  - PaneTarget: value object addressing ``session:window[.pane]``
  - TmuxLayout: abstract create_slot / kill_slot
  - WindowPerRoleLayout: default, one window per role (pane=None)
  - PaneGridLayout: skeleton — real split-window logic lives in Phase B
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest


# ----------------------------- PaneTarget -----------------------------


def test_pane_target_without_pane_produces_session_window_address():
    from zf.runtime.tmux_layout import PaneTarget
    target = PaneTarget(session="zf", window="dev")
    assert target.address() == "zf:dev"


def test_pane_target_with_pane_produces_session_window_pane_address():
    from zf.runtime.tmux_layout import PaneTarget
    target = PaneTarget(session="zf", window="roles", pane=2)
    assert target.address() == "zf:roles.2"


def test_pane_target_is_frozen():
    """PaneTarget is a value object, immutable once constructed."""
    from zf.runtime.tmux_layout import PaneTarget
    target = PaneTarget(session="zf", window="dev")
    with pytest.raises(FrozenInstanceError):
        target.window = "other"  # type: ignore[misc]


def test_pane_target_equality_and_hash():
    from zf.runtime.tmux_layout import PaneTarget
    a = PaneTarget(session="zf", window="dev", pane=0)
    b = PaneTarget(session="zf", window="dev", pane=0)
    c = PaneTarget(session="zf", window="dev", pane=1)
    assert a == b
    assert a != c
    assert {a, b, c} == {a, c}  # dedup via hash


# --------------------------- TmuxLayout ABC ---------------------------


def test_tmux_layout_is_abstract():
    from zf.runtime.tmux_layout import TmuxLayout
    with pytest.raises(TypeError):
        TmuxLayout()  # type: ignore[abstract]


def test_tmux_layout_declares_create_and_kill():
    from zf.runtime.tmux_layout import TmuxLayout
    assert hasattr(TmuxLayout, "create_slot")
    assert hasattr(TmuxLayout, "kill_slot")


# ---------------------- WindowPerRoleLayout ---------------------------


def _fake_role(name: str = "dev", instance_id: str | None = None):
    role = MagicMock()
    role.instance_id = instance_id or name
    role.name = name
    return role


def test_window_per_role_returns_pane_none(monkeypatch):
    from zf.runtime.tmux_layout import PaneTarget, WindowPerRoleLayout
    from zf.runtime.tmux import TmuxSession

    tmux = TmuxSession(session_name="zf-test", dry_run=True)
    layout = WindowPerRoleLayout()
    target = layout.create_slot(tmux, _fake_role("dev"))
    assert isinstance(target, PaneTarget)
    assert target.session == "zf-test"
    assert target.window == "dev"
    assert target.pane is None


def test_window_per_role_create_slot_issues_new_window():
    from zf.runtime.tmux_layout import WindowPerRoleLayout
    from zf.runtime.tmux import TmuxSession

    tmux = TmuxSession(session_name="zf-test", dry_run=True)
    layout = WindowPerRoleLayout()
    layout.create_slot(tmux, _fake_role("dev"))
    # At least one `tmux new-window` call in the log
    assert any("new-window" in cmd for cmd in tmux.command_log)


def test_window_per_role_kill_slot_issues_kill_window():
    from zf.runtime.tmux_layout import PaneTarget, WindowPerRoleLayout
    from zf.runtime.tmux import TmuxSession

    tmux = TmuxSession(session_name="zf-test", dry_run=True)
    layout = WindowPerRoleLayout()
    target = PaneTarget(session="zf-test", window="dev", pane=None)
    layout.kill_slot(tmux, target)
    assert any("kill-window" in cmd for cmd in tmux.command_log)


# --------------------------- PaneGridLayout ---------------------------
# Phase A keeps this as a skeleton — real split-window behavior lives
# in Phase B. Verify the class exists and returns panes, nothing more.


def test_pane_grid_layout_class_exists():
    from zf.runtime.tmux_layout import PaneGridLayout
    # Instantiating is legal even without Phase B impl
    layout = PaneGridLayout(window_name="roles")
    assert layout.window_name == "roles"


def test_pane_grid_layout_assigns_incrementing_pane_indices():
    """Phase A skeleton: track per-instance pane_index so Phase B just
    plugs in the subprocess calls.
    """
    from zf.runtime.tmux_layout import PaneGridLayout
    from zf.runtime.tmux import TmuxSession

    tmux = TmuxSession(session_name="zf-test", dry_run=True)
    layout = PaneGridLayout(window_name="roles")

    t1 = layout.create_slot(tmux, _fake_role("orchestrator"))
    t2 = layout.create_slot(tmux, _fake_role("dev"))
    t3 = layout.create_slot(tmux, _fake_role("review"))

    # All share the same window; pane index grows
    assert t1.window == t2.window == t3.window == "roles"
    assert (t1.pane, t2.pane, t3.pane) == (0, 1, 2)


def test_pane_grid_layout_reuses_pane_index_after_kill():
    """When a slot is killed the pane index is reclaimable. Simple
    policy: LRU — re-assign the lowest-free index on next create."""
    from zf.runtime.tmux_layout import PaneGridLayout
    from zf.runtime.tmux import TmuxSession

    tmux = TmuxSession(session_name="zf-test", dry_run=True)
    layout = PaneGridLayout(window_name="roles")

    t1 = layout.create_slot(tmux, _fake_role("a"))
    t2 = layout.create_slot(tmux, _fake_role("b"))
    layout.kill_slot(tmux, t1)
    t3 = layout.create_slot(tmux, _fake_role("c"))
    # Reuses index 0 that was freed
    assert t3.pane == 0
    assert t2.pane == 1
