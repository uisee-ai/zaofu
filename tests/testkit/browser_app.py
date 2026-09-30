"""Test-only app factory; no fixture routes are added to the production API."""

from __future__ import annotations

import os
from pathlib import Path

from zf.core.config.loader import load_config
from zf.web import server


def create_app():
    root = Path(os.environ["ZF_TESTKIT_BROWSER_ROOT"]).resolve()
    config = load_config(root / "zf.yaml")
    server._REACT_DIST_DIR = Path(os.environ["ZF_TESTKIT_BROWSER_DIST"])
    return server.create_app(
        root / config.project.state_dir,
        config=config,
        project_root=root,
    )
