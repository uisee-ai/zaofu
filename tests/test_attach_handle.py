"""1206 Phase B-T3 — TmuxTransport.attach_handle layout-aware.

window_per_role: ``tmux select-window -t session:instance_id``
pane_grid:      ``tmux select-pane  -t session:roles.pane_idx``
"""

from __future__ import annotations

from zf.core.config.schema import RoleConfig
from zf.runtime.tmux import TmuxSession
from zf.runtime.tmux_layout import PaneGridLayout, WindowPerRoleLayout
from zf.runtime.transport import TmuxTransport


def test_attach_handle_under_window_per_role_uses_select_window():
    tmux = TmuxSession(session_name="zf-t", dry_run=True)
    transport = TmuxTransport(tmux)
    role = RoleConfig(name="dev", backend="claude-code")
    transport.spawn(role, ["claude"])

    handle = transport.attach_handle("dev")
    assert handle.argv == ["tmux", "select-window", "-t", "zf-t:dev"], (
        f"window_per_role should select-window, got {handle.argv}"
    )


def test_attach_handle_under_pane_grid_uses_select_pane():
    layout = PaneGridLayout(window_name="roles")
    tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
    transport = TmuxTransport(tmux)
    # Spawn to allocate pane index
    role = RoleConfig(name="dev", backend="claude-code")
    transport.spawn(role, ["claude"])

    handle = transport.attach_handle("dev")
    # pane_grid mode: attach should select-pane at roles.0
    assert "select-pane" in handle.argv, (
        f"pane_grid should select-pane, got {handle.argv}"
    )
    assert "zf-t:roles.0" in handle.argv, (
        f"pane_grid should target roles.<pane_idx>, got {handle.argv}"
    )


def test_attach_handle_without_role_returns_session_attach():
    """No specific role → whole-session attach, same for both layouts."""
    for layout in (WindowPerRoleLayout(), PaneGridLayout(window_name="roles")):
        tmux = TmuxSession(session_name="zf-t", dry_run=True, layout=layout)
        transport = TmuxTransport(tmux)
        handle = transport.attach_handle(None)
        assert "attach-session" in handle.argv
        assert "zf-t" in handle.argv
