"""Dashboard preparation works without starting the project dispatcher."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_web_workflow_proposals import _write_config
from tests.test_workflow_prepare import _provider, QUESTION
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.runtime.workflow_requests import load_workflow_request
from zf.web.server import create_app


@pytest.mark.parametrize("action", ["workflow-request", "idea-to-product"])
def test_web_lifecycle_consumes_preparation_in_configured_state_without_runtime(
    tmp_path, monkeypatch, action
):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    calls = _provider(monkeypatch)
    config_path = tmp_path / "zf.yaml"
    _write_config(config_path)
    config_path.write_text(
        config_path.read_text().replace("state_dir: .zf", "state_dir: .custom-state")
    )
    state = tmp_path / ".custom-state"
    state.mkdir()
    (state / "events.jsonl").touch()
    (state / "kanban.json").write_text("[]")
    before = config_path.read_bytes()
    app = create_app(state, config=load_config(config_path), project_root=tmp_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/projects/default/actions/{action}",
            headers={"x-zf-web-token": "test-token"},
            json={
                "project_id": "default",
                "idempotency_key": "prepare-test",
                "actor": "web",
                "payload": {
                    "kind": "auto",
                    "objective": "Repair session expiry",
                    "backend": "mock",
                },
            },
        )
        assert response.status_code == 202, response.text
        rid = response.json()["request_id"]
        listing = client.get("/api/projects/default/workflow-requests").json()
        assert [item["request_id"] for item in listing["items"]] == [rid]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if load_workflow_request(state, rid).get("status") == "clarifying":
                break
            time.sleep(0.05)
        detail = client.get(f"/api/projects/default/workflow-requests/{rid}").json()
        assert detail["result"]["open_questions"] == [QUESTION], detail
        assert detail["result"]["origin_binding"]["project_id"]
    assert len(calls) == 1
    assert config_path.read_bytes() == before
    assert not (tmp_path / ".zf").exists()
    assert not any(
        e.type == "workflow.invoke.requested"
        for e in EventLog(state / "events.jsonl").read_all()
    )


def test_preparation_worker_services_every_open_project(tmp_path, monkeypatch):
    from zf.web.workflow_preparation_worker import WorkflowPreparationWorker

    calls = []
    monkeypatch.setattr(
        "zf.web.workflow_preparation_worker.consume_workflow_synthesis_operations",
        lambda **kwargs: calls.append(kwargs["project_root"]),
    )
    worker = WorkflowPreparationWorker()
    roots = {tmp_path / f"project-{i}" for i in range(4)}
    for root in roots:
        root.mkdir()
        _write_config(root / "zf.yaml")
        (root / ".zf").mkdir()
        (root / ".zf" / "events.jsonl").touch()
        worker.watch(root)
    worker.start()
    try:
        deadline = time.monotonic() + 6
        while set(calls) != roots and time.monotonic() < deadline:
            time.sleep(0.05)
        assert set(calls) == roots
    finally:
        worker.stop()
