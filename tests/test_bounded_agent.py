from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.workflow.bounded_agent import build_bounded_agent_spec
from zf.runtime.workflow_route_catalog import workflow_route_catalog


def write_config(tmp_path: Path, *, backend: str = "mock") -> Path:
    spec = build_bounded_agent_spec({"executor_role": "bounded-executor", "verifier_role": "bounded-verifier"})
    config = {"version": "1.0", "project": {"name": "bounded", "state_dir": ".runtime-test"},
              "workflow": {"execution_profiles": {"bounded": {"strategy": "direct", "limits": {
                  "timeout_seconds": 600, "token_budget": 100000, "max_usage_samples": 30}}}},
              "roles": [{"name": name, "role_kind": "reader", "backend": backend,
                         "provider_session": {"native_delegation": False},
                         "execution": {"default_profile": "bounded", "profile_allowlist": ["bounded"]}}
                        for name in ["bounded-executor", "bounded-verifier"]]}
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all([
        {"apiVersion": "zaofu.dev/v1", "kind": "Workflow", "metadata": {"name": "bounded-agent"}, "spec": spec},
        {"apiVersion": "zaofu.dev/v1", "kind": "ZfConfig", "metadata": {"name": "bounded"}, "spec": config},
    ], sort_keys=False))
    return path


def test_bounded_agent_compiles_to_one_executor_and_independent_verifier(tmp_path):
    config = load_config(write_config(tmp_path))
    route, = workflow_route_catalog(config)["routes"]
    assert route["route_id"] == "agent:single:agent-execute"
    assert route["executor_count"] == route["independent_verifier_count"] == 1
    assert route["stages"] == ["agent-execute", "agent-verify"]
    assert route["source_write"] is False
    assert route["verify_roles"] == ["bounded-verifier"]
    assert [s.timeout_seconds for s in config.workflow.stages] == [600, 600]


@pytest.mark.parametrize("fault", ["unbounded", "delegation", "same_verifier", "aliased_verifier",
                                  "extra_executor", "no_deadline", "source_writer", "aggregate_executor"])
def test_bounded_agent_rejects_unsafe_contracts(tmp_path, fault):
    path = write_config(tmp_path)
    docs = list(yaml.safe_load_all(path.read_text()))
    spec, config = docs[0]["spec"], docs[1]["spec"]
    if fault == "unbounded":
        config["workflow"]["execution_profiles"]["bounded"]["limits"]["token_budget"] = 0
    elif fault == "delegation":
        config["roles"][0].pop("provider_session")
    elif fault == "same_verifier":
        spec["tasks"][1]["role"] = spec["tasks"][0]["role"]
    elif fault == "aliased_verifier":
        config["roles"][0]["instance_id"] = "executor-alias"
        spec["tasks"][1]["role"] = "executor-alias"
    elif fault == "extra_executor":
        spec["tasks"][0]["fanout"] = {"roles": ["bounded-executor", "another"]}
        spec["tasks"][0].pop("role")
    elif fault == "source_writer":
        spec["tasks"][0]["operation"] = "agent.write"
    elif fault == "aggregate_executor":
        spec["tasks"][0]["aggregate"] = {"synthRole": "bounded-verifier"}
    else:
        spec["tasks"][0].pop("deadlineSeconds")
    path.write_text(yaml.safe_dump_all(docs))
    with pytest.raises(ConfigError):
        load_config(path)


def approved_runtime(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from zf.core.events import EventLog, EventWriter
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore
    from zf.core.workspace.project_initializer import ProjectInitializer
    from zf.runtime.workflow_start import WorkflowStartService
    from zf.runtime.orchestrator import Orchestrator
    from zf.web.server import create_app
    from tests.test_workflow_invoke_pattern_bridge import _RecordingTransport
    from tests.test_workflow_invoke_pattern_bridge import _run_until_sent

    path = write_config(tmp_path)
    config = load_config(path)
    ProjectInitializer().initialize(cwd=tmp_path, with_instruction_docs=False,
                                    with_git_hooks=False, workspace_register=False)
    state = tmp_path / config.project.state_dir
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="TASK-BOUNDED", title="Review a bounded requirement", contract=TaskContract(
        behavior="Produce a report about the supplied request", verification="Independent report review")))
    writer = EventWriter(EventLog(state / "events.jsonl"))
    # The recording transport replaces process launch; provision the same
    # role credentials as SpawnCoordinator before Kernel operation binding.
    from zf.runtime.result_submit import provision_role_submit_credential
    for role in config.roles:
        provision_role_submit_credential(state, role.instance_id)
    proposal = WorkflowStartService(state, config, project_root=tmp_path).propose(writer, {
        "task_id": "TASK-BOUNDED", "route_id": "agent:single:agent-execute",
        "objective": "Produce a report about the supplied request",
        "parameters": {"allow_missing_env": True, "backend": "mock"},
    }, actor="web", origin="web")
    assert proposal["ok"], proposal
    event = next(e for e in writer.event_log.read_all() if e.id == proposal["proposal_event_id"])
    payload = {**event.payload["proposal"]["payload"], "proposal_event_id": event.id}
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "bounded-test-token")
    client = TestClient(create_app(state, config=config, project_root=tmp_path))
    response = client.post("/api/actions/workflow-start", json=payload,
                           headers={"x-zf-web-token": "bounded-test-token"})
    assert response.status_code < 400, response.text
    replay = client.post("/api/actions/workflow-start", json=payload,
                         headers={"x-zf-web-token": "bounded-test-token"})
    assert replay.json()["status"] == "already_resolved"
    invokes = [e for e in writer.event_log.read_all() if e.type == "workflow.invoke.requested"]
    assert len(invokes) == 1
    class Transport(_RecordingTransport):
        def __init__(self):
            super().__init__()
            self.stopped = set()

        def terminate(self, role_name):
            self.stopped.add(role_name)

        def is_alive(self, role_name):
            return role_name not in self.stopped

    transport = Transport()
    runtime = Orchestrator(state, load_config(path), transport, project_root=tmp_path)
    decision = runtime._on_workflow_invoke_requested(invokes[0])
    assert decision is not None, [e.payload for e in writer.event_log.read_all() if e.type == "workflow.invoke.rejected"]
    accepted = [e for e in writer.event_log.read_all() if e.type == "workflow.invoke.accepted"]
    assert len(accepted) == 1
    _run_until_sent(runtime, transport, 1)
    assert len(transport.sent) == 1
    assert transport.sent[0][0] == "bounded-executor"
    runtime._on_workflow_invoke_requested(invokes[0])
    assert len([e for e in writer.event_log.read_all() if e.type == "workflow.invoke.accepted"]) == 1
    assert store.get("TASK-BOUNDED").status != "done"
    return state, writer, invokes[0], runtime, transport, client, store


def test_approved_bounded_agent_reaches_kernel_once_after_restart(tmp_path, monkeypatch):
    approved_runtime(tmp_path, monkeypatch)
