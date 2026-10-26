"""Controlled auto request -> durable synthesis -> proposal -> approved invoke."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_control_actions_workflow_request import _execute, _service
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.workflow_synthesis import consume_workflow_synthesis_operations


pytestmark = pytest.mark.mock_e2e


def payload():
    return {"request_id": "auto-handoff", "kind": "auto",
            "objective": "不要重构，只修复登录失败并验证过期返回401",
            "backend": "mock", "synthesis_backend": "mock", "allow_missing_env": True,
            "conversation_id": "kanban:demo", "thread_key": "login"}


def candidate(request):
    return {
        "schema_version": "workflow-synthesis-result.v1",
        "request_id": request["request_id"], "request_revision": request["revision"],
        "requirement_ref": request["requirement_spec_ref"], "requirement_digest": request["requirement_spec_digest"],
        "selected_flow_family": "IssueFlow",
        "short_flow_spec": {"flow_family": "IssueFlow", "purpose": "修复登录", "parameters": {"lanes": 1}},
        "decision_rationale": "明确拒绝重构，要求修复现有行为。",
        "assumptions": [], "open_questions": [], "requested_roles": [],
        "requested_skills": [], "requested_profiles": [], "completion_profile": {}, "risk_hints": [],
    }


def consume(service, agent):
    return consume_workflow_synthesis_operations(
        state_dir=service.state_dir, project_root=service.project_root,
        config=service.config, writer=service.writer, agent=agent, limit=1)


def test_auto_handoff_reuses_result_and_reaches_light_task_map_once(tmp_path):
    service, log = _service(tmp_path)
    queued = _execute(service, "workflow-request", payload())
    request = load_workflow_request(service.state_dir, queued["request_id"])
    calls = []

    class Agent:
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=True, reply=json.dumps(candidate(request)))

    assert consume(service, Agent()) == 1
    settled = load_workflow_request(service.state_dir, queued["request_id"])
    assert settled["status"] == "proposed", settled
    assert settled["origin_binding"] == queued["origin_binding"]
    replay = _execute(service, "workflow-request", payload())
    assert replay["synthesis_operation_id"] == queued["synthesis_operation_id"], replay
    assert replay["operation_status"] == "settled"
    consume(service, Agent())
    assert len(calls) == 1
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())
    submit = {"request_id": settled["request_id"], "intake_ref": queued["intake_ref"],
              "proposal_ref": settled["proposal_ref"], "proposal_digest": settled["proposal_digest"],
              "kind": "issue", "allow_missing_env": True}
    assert _execute(service, "workflow-submit", submit)["ok"]
    assert _execute(service, "workflow-submit", submit)["ok"]
    from zf.cli.flow import build_flow_intent

    before = load_workflow_request(service.state_dir, settled["request_id"])
    with pytest.raises(ValueError, match="active Workflow Request is immutable"):
        build_flow_intent(intake_path=Path(queued["intake_ref"]), explicit_kind="workflow")
    assert load_workflow_request(service.state_dir, settled["request_id"]) == before
    invokes = [e for e in log.read_all() if e.type == "workflow.invoke.requested"]
    assert len(invokes) == 1
    SessionStore(service.state_dir / "session.yaml").create(project_root=str(tmp_path))
    runtime = Orchestrator(service.state_dir, service.config,
                           TmuxTransport(TmuxSession(session_name="unused", dry_run=True)), project_root=tmp_path)
    runtime.run_once(events=[invokes[0]])
    entries = [e for e in log.read_all() if e.type == "issue.requested"]
    assert entries, [(e.type, e.payload.get("reason")) for e in log.read_all() if "invoke" in e.type]
    runtime.run_once(events=entries)
    maps = [e for e in log.read_all() if e.type == "task_map.ready"]
    assert len(maps) == 1
    assert maps[0].payload["requirement_spec_digest"] == settled["requirement_spec_digest"]


@pytest.mark.parametrize("fault", ["cancel", "revise", "provider_failure"])
def test_auto_late_or_failed_result_never_becomes_executable(tmp_path, fault):
    service, log = _service(tmp_path)
    queued = _execute(service, "workflow-request", payload())
    request = load_workflow_request(service.state_dir, queued["request_id"])

    class Agent:
        def run_turn(self, **kwargs):
            if fault == "cancel":
                assert _execute(service, "workflow-cancel", {
                    "request_id": queued["request_id"], "operation_id": queued["synthesis_operation_id"],
                    "request_hash": queued["synthesis_request_hash"], "reason": "Owner cancelled",
                })["ok"]
            elif fault == "revise":
                from zf.runtime.workflow_requests import revise_workflow_request
                revise_workflow_request(service.state_dir, Path(queued["workflow_input_manifest_ref"]),
                                        actor="operator", objective="只解释，不实施", confirm=True, writer=service.writer)
            else:
                return SimpleNamespace(ok=False, reply="", error="provider unavailable")
            return SimpleNamespace(ok=True, reply=json.dumps(candidate(request)))

    consume(service, Agent())
    current = load_workflow_request(service.state_dir, queued["request_id"])
    assert not current.get("proposal_ref")
    assert current["kind"] == "auto"
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())
    if fault == "cancel":
        assert load_workflow_operation(log, queued["synthesis_operation_id"])["status"] == "cancelled"
        replay = _execute(service, "workflow-request", payload())
        assert replay["operation_status"] == "cancelled"
        assert replay["synthesis_operation_id"] == queued["synthesis_operation_id"]
        assert consume(service, Agent()) == 0
