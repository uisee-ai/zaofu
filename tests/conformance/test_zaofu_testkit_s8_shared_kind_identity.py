"""Formal requests reject identity drift without fabricating invoke payloads."""

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from tests.test_control_actions_workflow_request import _execute
from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.workflow_delivery import apply_flow_submit


@pytest.fixture
def shared(tmp_path, monkeypatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    route.start()
    with route.evidence.guard("formal_identity_negative", {}):
        yield route
        assert not forbidden


@pytest.mark.parametrize("fault", ["different_config", "missing_snapshot", "tampered_snapshot", "tampered_contract"])
def test_formal_second_run_config_or_contract_fault_cannot_dispatch(shared, fault):
    route = shared
    if fault == "different_config":
        docs = list(yaml.safe_load_all(route.config_path.read_text()))
        [config] = [doc["spec"] for doc in docs if doc["kind"] == "ZfConfig"]
        config["quality_gates"]["test"]["required_checks"].append("true")
        route.config_path.write_text(yaml.safe_dump_all(docs, sort_keys=False))
        route.reopen()
    second = route.request_delivery("issue", scope=("answer.txt",))
    if fault != "different_config":
        path = route.state / (second.payload["run_contract_ref"] if fault == "tampered_contract"
                              else second.payload["effective_config_ref"]["ref"])
        if fault == "missing_snapshot":
            path.unlink()
        else:
            path.write_text("{}")
    route.tick()
    run_id = second.payload["workflow_run_id"]
    projection = build_run_admission_projection(route.log.read_all())
    assert projection.active_run_ids == [route.invoke.payload["workflow_run_id"]]
    if fault == "tampered_contract":
        [rejection] = route.events("workflow.invoke.rejected")
        assert rejection.payload["source_event_id"] == second.id
        assert rejection.payload["reason"] == "approved proposal binding cannot be verified"
        assert projection.runs[run_id].status == "rejected"
    else:
        [queued] = route.events("run.admission.queued")
        assert queued.payload["source_event_id"] == second.id
        assert queued.payload["reason"] == (
            "concurrent effective config digest mismatch" if fault == "different_config"
            else "concurrent effective config binding invalid"
        )
        assert projection.queued_run_ids == [run_id]
    before = {kind: [event.id for event in route.events(kind)] for kind in (
        "run.admission.admitted", "run.admission.queued", "run.admission.rejected",
        "workflow.invoke.rejected", "fanout.child.dispatched",
    )}
    route.reopen()
    for _ in range(3):
        route.tick()
    assert before == {kind: [event.id for event in route.events(kind)] for kind in before}
    assert not any(event.payload.get("trace_id") == run_id for event in route.events("fanout.child.dispatched"))


@pytest.mark.parametrize("entry", ["controlled", "cli"])
def test_formal_task_collision_does_not_replace_active_owner(shared, entry):
    route = shared
    for _ in range(2):
        route.tick()
    task_id = route.invoke.task_id
    before_task = asdict(route.runtime.task_store.get(task_id))
    proposal = _execute(route.service, "workflow-request", {
        "kind": "issue", "objective": "Set answer.txt to fixed and pass tests/verify_issue.py",
        "backend": "codex", "allow_missing_env": True, "scope": ["answer.txt"],
    })
    assert proposal["ok"] and proposal["status"] == "proposal_ready", proposal
    if entry == "controlled":
        result = _execute(route.service, "workflow-submit", {
            **{key: proposal[key] for key in ("intake_ref", "request_id", "proposal_ref", "proposal_digest")},
            "kind": "issue", "allow_missing_env": True, "task_id": task_id,
        })
        assert not result["ok"] and result["status"] == "workflow_task_stale", result
        assert len(route.events("workflow.invoke.requested")) == 1
    else:
        result = apply_flow_submit(
            config_path=route.config_path, intake_path=Path(proposal["intake_ref"]),
            flow_kind="issue", task_id=task_id, requested_by="test", allow_missing_env=True,
        )
        assert result["status"] == "STOP", result
        assert any(item["kind"] == "workflow_task_stale" for item in result["blockers"])
    assert asdict(route.runtime.task_store.get(task_id)) == before_task
    assert len(route.events("workflow.invoke.requested")) == 1
    route.reopen()
    for _ in range(3):
        route.tick()
    assert asdict(route.runtime.task_store.get(task_id)) == before_task
    assert build_run_admission_projection(route.log.read_all()).active_run_ids == [route.invoke.payload["workflow_run_id"]]
    assert {event.payload["trace_id"] for event in route.events("fanout.child.dispatched")} == {route.invoke.payload["workflow_run_id"]}
