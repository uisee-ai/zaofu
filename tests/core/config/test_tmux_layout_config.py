"""1206 Phase A-T3 — SessionConfig.tmux_layout yaml parsing + validation.

session:
  tmux_session: zf
  tmux_layout: window_per_role | pane_grid

Default is ``window_per_role`` so all existing yamls stay binary-identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import SessionConfig


def test_session_config_defaults_to_window_per_role():
    cfg = SessionConfig()
    assert cfg.tmux_layout == "window_per_role"


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


def test_loader_defaults_when_tmux_layout_omitted(tmp_path: Path):
    """Existing yamls without tmux_layout keep window_per_role semantics."""
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
    assert config.session.tmux_layout == "window_per_role"


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
