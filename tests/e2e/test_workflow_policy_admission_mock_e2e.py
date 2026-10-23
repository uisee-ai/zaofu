"""CLI/HTTP approval -> policy fence; no provider is invoked on stale approval."""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.test_workflow_start_service import _project
from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.runtime.workflow_start import WorkflowStartService
from zf.web.server import create_app

pytestmark = pytest.mark.mock_e2e


@pytest.mark.parametrize("surface", ["cli", "web"])
@pytest.mark.parametrize("change", ["budget", "permission", "approval"])
def test_old_approval_cannot_start_changed_policy(tmp_path: Path, monkeypatch, capsys,
                                                 surface: str, change: str) -> None:
    root, state = _project(tmp_path)
    config = load_config(root / "zf.yaml")
    log = EventLog(state / "events.jsonl")
    service = WorkflowStartService(state, config, project_root=root)
    proposed = service.propose(EventWriter(log), {
        "task_id": "TASK-WORKFLOW-START", "route_id": "research:fixed",
        "objective": "Compare execution boundaries",
    }, actor="web", origin="web")
    assert proposed["ok"], proposed
    event = next(e for e in log.read_all() if e.id == proposed["proposal_event_id"])
    proposal = event.payload["proposal"]
    if change == "budget":
        config.global_budget_usd = 0.1
    elif change == "permission":
        config.roles[0].permission_mode = "restricted"
    else:
        config.workflow.plan_approval_enabled = not config.workflow.plan_approval_enabled
    from zf.core.config.render import renderable_config_to_primitive
    (root / "zf.yaml").write_text(yaml.safe_dump(renderable_config_to_primitive(config)))
    if surface == "cli":
        monkeypatch.chdir(root)
        monkeypatch.setenv("ZF_WORKFLOW_ACTION_TOKEN", "isolated-policy-test")
        result = main(["workflow", "start", "--apply", "--proposal-event-id", event.id,
                       "--authorization-ref", "operator:test", "--authorization-token",
                       "isolated-policy-test", "--format", "json"])
        assert result != 0
        assert "workflow_route_unavailable" in capsys.readouterr().out
    else:
        monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "isolated-policy-test")
        client = TestClient(create_app(state, config=config, project_root=root))
        response = client.post("/api/actions/workflow-start", json={
            **proposal["payload"], "proposal_event_id": event.id,
        }, headers={"x-zf-web-token": "isolated-policy-test"})
        assert response.status_code == 409, response.text
        assert response.json()["status"] == "workflow_route_unavailable"
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())
