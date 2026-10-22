"""Pending Plan advice uses the same bound artifact roots as its preview."""
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.web.plan_health_routes import build_plan_health_router


@pytest.mark.parametrize("location", ["state", "project", "outside"])
def test_pending_plan_resolves_custom_state_and_preserves_project_boundary(tmp_path, location):
    project = tmp_path / "project"
    state = project / "runtime-data"
    state.mkdir(parents=True)
    base = {"state": state, "project": project, "outside": tmp_path}[location]
    target = base / "artifacts/task_map.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"tasks": [{
        "task_id": "core", "allowed_paths": ["app/core/**"],
        "verification": "python -m pytest",
    }]}))
    ref = str(target) if location == "outside" else "artifacts/task_map.json"
    log = EventLog(state / "events.jsonl")
    EventWriter(log).emit("plan.approval.requested", actor="test", payload={
        "plan_id": "plan-1", "task_map_ref": ref,
    })
    before = (state / "events.jsonl").read_bytes()
    ctx = SimpleNamespace(state_dir=state, project_root=project,
                          config=ZfConfig(project=ProjectConfig(name="test")))
    app = FastAPI()
    app.include_router(build_plan_health_router(resolve_ctx=lambda _: ctx))
    with TestClient(app) as client:
        response = client.get("/api/projects/default/plan/pending")
    assert response.status_code == 200
    [pending] = response.json()["pending"]
    assert pending["advice"]["binding"] is False
    if location == "outside":
        assert pending["advice"]["advice"] == "reject"
        assert "outside the current project" in str(pending["checklist_warnings"])
    else:
        assert pending["checklist_warnings"] == []
        assert pending["advice"]["advice"] == "approve"
    assert (state / "events.jsonl").read_bytes() == before
