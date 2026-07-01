from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.runtime_resources import build_runtime_resource_projection


def _state(tmp_path: Path) -> tuple[Path, EventLog, Path]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    transcript = state_dir / "operator" / "dev-1.log"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "starting\nAPI_TOKEN=secret-value\nfinished\n",
        encoding="utf-8",
    )
    (state_dir / "session.yaml").write_text(
        "runtime_state: running\nsession_id: demo\n",
        encoding="utf-8",
    )
    (state_dir / "role_sessions.yaml").write_text(yaml.safe_dump({
        "project_root": str(tmp_path),
        "roles": {"dev-1": "11111111-1111-1111-1111-111111111111"},
        "instance_meta": {
            "dev-1": {
                "backend": "codex",
                "spawned_at": "2026-06-15T00:00:00+00:00",
                "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "last_heartbeat_payload": {
                    "current_task_id": "TASK-1",
                    "state": "busy",
                },
                "provider_pid": 4242,
                "session_path": str(transcript),
                "tmux_session": "cd-0",
            },
        },
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev-1",
        task_id="TASK-1",
        payload={"to": "busy"},
    ))
    return state_dir, log, transcript


def test_runtime_resources_project_sessions_terminal_and_tmux(tmp_path: Path) -> None:
    state_dir, log, transcript = _state(tmp_path)

    projection = build_runtime_resource_projection(
        state_dir,
        project_root=tmp_path,
        events=log.read_all(),
        tmux_output="cd-0\t1\t3\nother\t0\t1\n",
    )

    session = projection["provider_sessions"][0]
    excerpt = projection["terminal_excerpts"][0]

    assert projection["schema_version"] == "runtime-resources.v1"
    assert projection["summary"]["provider_sessions"] == 1
    assert session["instance_id"] == "dev-1"
    assert session["backend"] == "codex"
    assert session["task_id"] == "TASK-1"
    assert session["provider_pid"] == 4242
    assert session["session_ref"]["exists"] is True
    assert session["session_ref"]["path_hash"]
    assert excerpt["status"] == "ok"
    assert excerpt["path_hash"] == session["session_ref"]["path_hash"]
    assert excerpt["excerpt_sha256"]
    assert "secret-value" not in excerpt["excerpt"]
    assert "[REDACTED_SECRET]" in excerpt["excerpt"]
    assert transcript.read_text(encoding="utf-8").endswith("finished\n")
    assert projection["host"]["tmux"]["configured_active"] is False
    assert projection["host"]["tmux"]["sessions"][0]["session_name"] == "cd-0"
    assert projection["host"]["tmux"]["sessions"][0]["attached"] is True


def test_runtime_resources_web_api_and_snapshot(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    state_dir, _, _ = _state(tmp_path)
    client = TestClient(create_app(state_dir, project_root=tmp_path))
    project_id = client.get("/api/snapshot").json()["project"]["project_id"]

    response = client.get(f"/api/projects/{project_id}/runtime/resources")
    assert response.status_code == 200
    assert response.json()["summary"]["provider_sessions"] == 1

    snapshot = client.get("/api/snapshot").json()
    resources = snapshot["runtime"]["resources"]
    assert resources["schema_version"] == "runtime-resources.v1"
    assert resources["provider_sessions"][0]["instance_id"] == "dev-1"
    assert "secret-value" not in str(resources)
