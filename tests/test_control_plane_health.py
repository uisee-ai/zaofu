from __future__ import annotations

import json
import os
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.control_plane_health import (
    build_control_plane_health,
    write_control_plane_health_projection,
)


def test_control_plane_health_projects_watcher_run_manager_and_autoresearch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    (state_dir / "processes").mkdir(parents=True)
    (state_dir / "processes" / "watcher.pid.json").write_text(
        json.dumps({
            "owner_pid": os.getpid(),
            "component": "watcher",
            "epoch": "epoch-1",
            "heartbeat_at": "2026-07-15T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    events = [
        ZfEvent(id="rm-1", type="run.manager.tick.completed"),
        ZfEvent(id="ar-1", type="autoresearch.trigger.accepted"),
    ]

    projection = build_control_plane_health(state_dir, events)
    path = write_control_plane_health_projection(state_dir, events)

    assert projection["components"]["watcher"]["status"] == "healthy"
    assert projection["components"]["watcher"]["epoch"] == "epoch-1"
    assert projection["components"]["run_manager"]["last_event_id"] == "rm-1"
    assert projection["components"]["autoresearch"]["last_event_id"] == "ar-1"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "control-plane-health.v1"
