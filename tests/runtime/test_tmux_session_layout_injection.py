"""1206 Phase A-T2 — TmuxSession accepts a layout kwarg without changing
existing behavior for callers that don't pass one.
"""

from __future__ import annotations

import pytest

from zf.runtime.tmux import TmuxSession


def test_tmux_session_defaults_to_window_per_role_layout():
    from zf.runtime.tmux_layout import WindowPerRoleLayout
    sess = TmuxSession(session_name="zf-t", dry_run=True)
    assert isinstance(sess.layout, WindowPerRoleLayout)


def test_tmux_session_accepts_custom_layout():
    from zf.runtime.tmux_layout import PaneGridLayout
    layout = PaneGridLayout(window_name="roles")
    sess = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    assert sess.layout is layout


def test_existing_create_window_still_works_without_layout():
    """Regression: old callers that construct TmuxSession(name, dry_run)
    without layout kwarg continue to create one window per call.
    """
    sess = TmuxSession(session_name="zf-t", dry_run=True)
    sess.create_window("dev")
    assert any("new-window" in c and "dev" in c for c in sess.command_log)
