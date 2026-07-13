"""1206 Phase A-T3 — SessionConfig.tmux_layout yaml parsing + validation.

session:
  tmux_session: zf
  tmux_layout: window_per_role | pane_grid

Default is ``pane_grid`` (2026-07-09) — one window, one pane per role — so
configs that omit tmux_layout no longer fall back to the legacy multi-window
layout. Still overridable per config/profile (set ``window_per_role``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import SessionConfig


def test_session_config_defaults_to_pane_grid():
    cfg = SessionConfig()
    assert cfg.tmux_layout == "pane_grid"


def test_loader_parses_window_per_role(tmp_path: Path):
    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: test-proj
session:
  tmux_session: zf
  tmux_layout: window_per_role
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    config = load_config(yaml_path)
    assert config.session.tmux_layout == "window_per_role"


def test_loader_parses_pane_grid(tmp_path: Path):
    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: test-proj
session:
  tmux_session: zf
  tmux_layout: pane_grid
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    config = load_config(yaml_path)
    assert config.session.tmux_layout == "pane_grid"


def test_loader_defaults_to_pane_grid_when_tmux_layout_omitted(tmp_path: Path):
    """Yamls without tmux_layout default to pane_grid (one window, panes)."""
    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: test-proj
session:
  tmux_session: zf
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    config = load_config(yaml_path)
    assert config.session.tmux_layout == "pane_grid"


def test_loader_rejects_unknown_layout(tmp_path: Path):
    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: test-proj
session:
  tmux_session: zf
  tmux_layout: nonsense-layout
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    with pytest.raises(ConfigError, match="tmux_layout"):
        load_config(yaml_path)
