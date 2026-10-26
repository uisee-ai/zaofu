"""A later, exact Owner start approval does not rewrite a planning-only PRD."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from zf.web.server import create_app

from tests.test_channel_prd_task_contract import _ready_prd_fixture, _task_payload, _execute
from zf.core.config.loader import load_config
from zf.core.task.store import TaskStore
from zf.runtime.channel_execution_approval import channel_start_authorization_error
from zf.runtime.workflow_start import WorkflowStartService

ROOT = Path(__file__).resolve().parents[1]


def _prepared(tmp_path: Path):
    state, writer, service, authority = _ready_prd_fixture(tmp_path, implementation_start=False)
    for name in ("examples", "skills"):
        (tmp_path / name).symlink_to(ROOT / name, target_is_directory=True)
    (tmp_path / "zf.yaml").write_text((ROOT / "zf.yaml").read_text())
    service.config = load_config(tmp_path / "zf.yaml")
    result = _execute(service, writer, "create-task", _task_payload(authority))
    assert result["ok"], result
    task = TaskStore(state / "kanban.json").get(result["task_id"])
    proposal = WorkflowStartService(state, service.config, project_root=tmp_path).propose(
        writer, {"task_id": task.id, "route_id": "delivery:prd:standard",
                 "objective": task.contract.behavior,
                 "parameters": {"backend": "mock", "target_root": str(tmp_path),
                                "authorize_channel_execution": True}},
        actor="web", origin="web")
    assert proposal["ok"], proposal
    event = next(e for e in writer.event_log.read_all() if e.id == proposal["proposal_event_id"])
    payload = {**event.payload["proposal"]["payload"], "proposal_event_id": event.id}
    return state, writer, service, task, payload


@pytest.mark.parametrize("fault", ["none", "non_owner", "missing_proposal", "changed_payload", "leader", "prd", "readiness"])
def test_owner_execution_is_bound_to_current_exact_proposal(tmp_path: Path, fault: str) -> None:
    state, writer, service, task, payload = _prepared(tmp_path)
    actor = "web"
    if fault == "non_owner":
        actor = "leader-1"
    elif fault == "missing_proposal":
        payload.pop("proposal_event_id")
    elif fault == "changed_payload":
        payload["objective"] = "another scope"
    elif fault in {"leader", "prd"}:
        field = "leader_revision" if fault == "leader" else "prd_revision"
        task.contract.evidence_contract[field] = 99
    elif fault == "readiness":
        task.contract.evidence_contract["readiness_verdict"] = "needs_owner"
    error = channel_start_authorization_error(
        state_dir=state, task=task, actor=actor, payload=payload, events=writer.event_log.read_all())
    assert bool(error) == (fault != "none"), error
    assert not any(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all())
    assert task.contract.evidence_contract["implementation_start"] is False


def test_controlled_start_accepts_later_owner_approval_and_preserves_prd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("zf.runtime.preflight._probe_provider_auth", lambda _: (True, "mock auth"))
    state, writer, service, task, payload = _prepared(tmp_path)
    prd_path = state / task.contract.source_ref
    before = prd_path.read_bytes()
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "channel-execution-test")
    client = TestClient(create_app(state, config=service.config, project_root=tmp_path))
    response = client.post("/api/actions/workflow-start", json=payload,
                           headers={"x-zf-web-token": "channel-execution-test"})
    started = response.json()
    assert response.status_code < 400 and started["ok"], started
    replay = client.post("/api/actions/workflow-start", json=payload,
                         headers={"x-zf-web-token": "channel-execution-test"})
    assert replay.json()["status"] == "already_resolved", replay.text
    assert sum(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all()) == 1
    assert prd_path.read_bytes() == before
    current = TaskStore(state / "kanban.json").get(task.id)
    assert current.contract.evidence_contract["implementation_start"] is False
    assert current.contract.evidence_contract["readiness_risk_accepted"] is False


@pytest.mark.parametrize("value", ["true", 1, {}, []])
def test_execution_authorization_requires_boolean(value):
    from zf.runtime.task_workflow_plans import normalize_task_workflow_parameters
    _, error = normalize_task_workflow_parameters({"authorize_channel_execution": value})
    assert error == "authorize_channel_execution must be a boolean"
