"""1206 Phase C wire-up — ``make_transport`` must honor
``config.session.tmux_layout``.

Without this wire-up the Phase A/B abstraction is unreachable from a
real ``zf start`` run: the session is always constructed with the
default ``WindowPerRoleLayout`` regardless of yaml.
"""

from __future__ import annotations

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.runtime.tmux_layout import PaneGridLayout, WindowPerRoleLayout
from zf.runtime.transport import CompositeTransport, TmuxTransport, make_transport


def _config(layout: str) -> ZfConfig:
    return ZfConfig(
        version="1.0",
        project=ProjectConfig(name="x"),
        session=SessionConfig(tmux_session="zf-t", tmux_layout=layout),
        roles=[RoleConfig(name="dev", backend="claude-code")],
    )


def test_default_window_per_role_wires_window_per_role_layout():
    t = make_transport(_config("window_per_role"), dry_run=True)
    assert isinstance(t, CompositeTransport)
    inner = t.for_role("dev")
    assert isinstance(inner, TmuxTransport)
    assert isinstance(inner.tmux.layout, WindowPerRoleLayout)


def test_pane_grid_yaml_wires_pane_grid_layout():
    t = make_transport(_config("pane_grid"), dry_run=True)
    inner = t.for_role("dev")
    assert isinstance(inner, TmuxTransport)
    assert isinstance(inner.tmux.layout, PaneGridLayout)


def test_pane_grid_all_tmux_roles_share_the_same_layout_instance():
    """Every TmuxTransport routed by CompositeTransport must share ONE
    PaneGridLayout so their _panes dict is consistent. If each
    TmuxTransport got its own layout, pane indices would collide."""
    cfg = _config("pane_grid")
    cfg.roles.append(RoleConfig(name="review", backend="claude-code"))
    t = make_transport(cfg, dry_run=True)

    dev_layout = t.for_role("dev").tmux.layout
    review_layout = t.for_role("review").tmux.layout
    assert dev_layout is review_layout, (
        "all tmux roles must share a single PaneGridLayout so panes "
        "are consistently numbered across roles"
    )
