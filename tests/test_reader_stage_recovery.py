"""Repaired reader infrastructure resumes through the existing controlled action."""
from pathlib import Path

import pytest

from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.control_actions import ControlledActionService


@pytest.fixture
def recovery(tmp_path):
    state = tmp_path / "runtime"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    config = ZfConfig()
    config.workflow.plan_approval_enabled = True
    config.workflow.stages = [WorkflowStageConfig(
        id="custom-plan", trigger="custom.scan.completed", topology="fanout_reader", attempt_domain="plan",
        roles=["planner"], aggregate=FanoutAggregateConfig(
            success_event="task_map.ready", failure_event="custom.plan.failed"))]
    writer.emit("run.admission.admitted", payload={"workflow_run_id": "run-1"})
    trigger = writer.emit("custom.scan.completed", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "pdd_id": "P1", "target_ref": "HEAD"})
    writer.emit("fanout.started", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "stage_id": "custom-plan", "fanout_id": "f1", "trigger_event_id": trigger.id})
    failure = writer.emit("custom.plan.failed", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "stage_id": "custom-plan", "fanout_id": "f1", "pdd_id": "P1",
        "failure_kind": "infra", "reason": "pinned input read failed"})
    service = ControlledActionService(state, writer, config=config, project_root=tmp_path, actor="operator")
    return service, failure, trigger


def execute(service, failure_id, **overrides):
    payload = {"repaired_failure_event_id": failure_id, "approval_ref": "owner-repair-approval",
               "reason": "pinned reader fix verified; repeat review, preserve approval gates", **overrides}
    requested = service.writer.emit("control.action.requested", actor="operator", payload=payload)
    return service.execute(action="stage-replan-new-generation", requested_action="stage-replan-new-generation",
                           payload=payload, requested=requested)


def test_explicit_repaired_reader_retry_keeps_run_inputs_and_replays_once(recovery):
    service, failure, original = recovery
    result = execute(service, failure.id)
    assert result["ok"], result
    triggers = [e for e in service.writer.event_log.read_all() if e.type == original.type]
    assert len(triggers) == 2
    assert triggers[-1].payload["workflow_run_id"] == "run-1"
    assert triggers[-1].payload["target_ref"] == "HEAD"
    assert triggers[-1].payload["repaired_failure_event_id"] == failure.id
    assert triggers[-1].payload["stage_replan_generation"]
    assert service.config.workflow.plan_approval_enabled
    assert not execute(service, failure.id)["ok"]
    assert len([e for e in service.writer.event_log.read_all() if e.type == original.type]) == 2


@pytest.mark.parametrize("fault", ["unknown", "no_authorization", "semantic", "terminal", "newer_fanout", "newer_dispatch"])
def test_reader_retry_rejects_stale_or_unauthorized_failure(recovery, fault):
    service, failure, original = recovery
    writer = service.writer
    overrides = {}
    if fault == "unknown": failure.id = "missing"
    elif fault == "no_authorization": overrides["approval_ref"] = ""
    elif fault == "semantic":
        failure = writer.emit(failure.type, correlation_id="run-1", payload={**failure.payload, "failure_kind": "quality"})
    elif fault == "terminal": writer.emit("run.cancelled", payload={"workflow_run_id": "run-1"})
    elif fault == "newer_fanout":
        writer.emit("fanout.started", payload={"workflow_run_id": "run-1", "stage_id": "custom-plan", "fanout_id": "f2"})
    else: writer.emit("fanout.synth.dispatched", payload={"fanout_id": "f1"})
    assert not execute(service, failure.id, **overrides)["ok"]
    assert len([e for e in writer.event_log.read_all() if e.type == original.type]) == 1


def test_reader_repair_web_entry_validates_authorization_and_retries(recovery, monkeypatch):
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    service, failure, original = recovery
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(service.state_dir, config=service.config,
                                   project_root=service.state_dir.parent))
    payload = {"repaired_failure_event_id": failure.id, "approval_ref": "owner-repair-approval",
               "reason": "input resolver fixed; repeat review"}
    endpoint = "/api/actions/stage.replan.new_generation"
    assert client.post(endpoint, json=payload).status_code == 403
    headers = {"x-zf-web-token": "test-token"}
    rejected = client.post(endpoint, headers=headers, json={**payload, "reason": ""})
    assert rejected.status_code == 409, rejected.text
    rejected = client.post(endpoint, headers=headers, json={**payload, "approval_ref": ""})
    assert rejected.status_code == 409, rejected.text
    response = client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["ok"]
    triggers = [e for e in service.writer.event_log.read_all() if e.type == original.type]
    assert len(triggers) == 2
    assert triggers[-1].payload["workflow_run_id"] == "run-1"


def test_operator_intent_payload_accepts_both_recovery_contracts():
    from zf.runtime.control_actions_helpers import validate_shared_action_payload

    validate = lambda payload: validate_shared_action_payload("stage-replan-new-generation", payload)
    repair = {"repaired_failure_event_id": "evt-failed", "reason": "resolver repaired", "approval_ref": "owner"}
    assert not validate(repair)
    assert not validate({"escalation_event_id": "evt-cap", "approval_ref": "owner"})
    assert validate({**repair, "reason": ""})
    assert validate({**repair, "approval_ref": ""})
    assert validate({"approval_ref": "owner"})
