"""Isolated real API fixture for Docker Playwright Owner-deferral verification.

Run with PYTHONPATH=src:. .venv/bin/python tests/e2e/channel_collaboration_browser_fixture.py
--root /tmp/zf-channel-collaboration-<UTC> --port 8002; never use the live port 8001.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path

import uvicorn

from tests.test_channel_collaboration_closure import CH, seed
from zf.core.config.loader import load_config
from zf.core.task.store import TaskStore
from zf.core.workspace.onboarding import apply_action
from zf.web.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.parent != Path("/tmp") or not root.name.startswith("zf-") or args.port < 8002:
        parser.error("use /tmp/zf-<purpose>-<UTC> and a port >= 8002")
    if root.exists():
        parser.error("fixture root must be new; choose a fresh UTC suffix")
    root.mkdir(parents=True)
    os.environ["ZF_WORKSPACE_HOME"] = str(root / "workspace")
    os.environ["ZF_WEB_ACTION_TOKEN"] = "zf-isolated-collaboration-browser"
    apply_action("skip")
    config_path = root / "zf.yaml"
    state, _, writer, _ = seed(root)
    config_path.write_text(f"version: '1.0'\nproject:\n  name: collaboration-browser\n  state_dir: {state.name}\n")
    store = TaskStore(state / "kanban.json")
    with store._locked():
        store._save_raw([])
    writer.emit("simulation.started", actor="browser-fixture", payload={"purpose": "Owner deferral through real Web/API"})
    writer.emit("channel.message.posted", actor="web", payload={
        "channel_id": CH, "thread_id": "main", "message_id": "requirement", "member_id": "operator",
        "role": "user", "text": "Preserve database requirements and record explicit Owner deferral.", "source": "test",
    })
    app = create_app(state, project_root=root, config=load_config(config_path))
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application) as lifespan_state:
                yield lifespan_state
        finally:
            writer.emit("simulation.done", actor="browser-fixture",
                        payload={"purpose": "Owner deferral browser fixture stopped"})

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
