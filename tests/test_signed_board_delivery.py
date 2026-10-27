"""Signed events stay verified through Board and Delivery projection paths."""
import json

from fastapi.testclient import TestClient

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web.projections.summaries import _archive_tasks, _delivery_features
from zf.web.server import create_app
from tests.dashboard_projection_helpers import ready_get


def test_signed_events_are_verified_in_archives_and_delivery_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_TEST_PROJECTION_SECRET", "fixture-only-key")
    config = ZfConfig()
    config.security.event_signing.enabled = True
    config.security.event_signing.secret_env = "ZF_TEST_PROJECTION_SECRET"
    state = tmp_path / "state"
    state.mkdir()
    tasks = TaskStore(state / "kanban.json")
    for task_id in ("BAD", "GOOD"):
        tasks.add(Task(id=task_id, title=task_id))
        tasks.update(task_id, status="done")
    log = event_log_from_project(state, config=config)
    log.append(ZfEvent(type="dev.build.done", task_id="BAD", payload={"fanout_id": "original"}))
    log.append(ZfEvent(type="verify.passed", task_id="GOOD", payload={"fanout_id": "valid"}))
    path = state / "events.jsonl"
    lines = path.read_text().splitlines()
    envelope = json.loads(lines[0])
    envelope["event"]["type"] = "verify.passed"
    envelope["event"]["payload"]["fanout_id"] = "forged"
    lines[0] = json.dumps(envelope)
    path.write_text("\n".join(lines) + "\n")

    # Legacy unsigned callers remain supported, and their cached values must
    # not be reused for a signing-enabled caller (or vice versa).
    assert "forged" in {row["id"] for row in _delivery_features(state)}
    assert "forged" not in {row["id"] for row in _delivery_features(state, config=config)}
    assert "forged" in {row["id"] for row in _delivery_features(state)}
    assert {row["id"] for row in _archive_tasks(state)} == {"BAD", "GOOD"}

    with TestClient(create_app(state, project_root=tmp_path, config=config)) as client:
        for url in ("/api/projects/default/board-cards", "/api/projects/default/snapshot/board"):
            response = ready_get(client, url)
            assert response.status_code == 200, response.text
            archive = {row["id"]: row for row in response.json()["archive_tasks"]}
            assert archive["BAD"]["workflow_projection"]["verify_state"] != "passed"
            assert archive["GOOD"]["workflow_projection"]["verify_state"] == "passed"
        response = client.get("/api/projects/default/delivery-features")
        assert response.status_code == 200, response.text
        ids = {row["id"] for row in response.json()["delivery_features"]}
        assert "forged" not in ids and "valid" in ids
