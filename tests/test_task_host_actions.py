"""Reviewed host assistance binds effects to a quiescent, current task."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import build_task_contract_snapshot, write_task_contract_snapshot
from zf.runtime.task_host_actions import apply
from zf.runtime.task_host_manifest import binding, digest, git, preview


@pytest.fixture
def case(tmp_path, monkeypatch):
    def command(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    command("init", "-b", "main")
    command("config", "user.name", "Host Assist Test")
    command("config", "user.email", "test@example.invalid")
    (tmp_path / ".gitignore").write_text(".state/\n")
    command("add", "--", ".gitignore")
    command("commit", "-m", "chore: seed")
    state = tmp_path / ".state"
    root = state / "workdirs/task/project"
    command("worktree", "add", "-b", "worker-test", str(root))
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("run.admission.admitted", payload={"workflow_run_id": "run-1"})
    config = SimpleNamespace(runtime=SimpleNamespace(workdirs=SimpleNamespace(required_paths=[], provision_paths=[])))
    service = ControlledActionService(state, writer, project_root=tmp_path, config=config, actor="operator")
    task = Task(id="T1", status="in_progress", contract=TaskContract(
        acceptance="probe returns success", verification="python3 probe.py",
        evidence_contract={"task_map_generation": "map-1"}))
    TaskStore(state / "kanban.json").add(task)
    snapshot = build_task_contract_snapshot(task, workflow_run_id="run-1", task_map_generation_id="map-1",
                                            base_commit=git(root, "rev-parse", "HEAD"), task_ref="task/T1")
    descriptor = write_task_contract_snapshot(state, snapshot)
    identity = {"operation_id": "op-1", "task_id": "T1", "workflow_run_id": "run-1",
                "task_map_generation": "map-1", "task_pipeline_stage": "impl", "operation_generation": 1,
                "active_attempt_id": "attempt-1", "lease_id": "lease-1", "dispatch_id": "dispatch-1"}
    request = {"result_identity": {"source_branch": "worker-test", "contract_snapshot_ref": descriptor["ref"],
                                  "contract_snapshot_digest": descriptor["sha256"]},
               "workdir_write_scopes": ["probe.py"]}
    ref = write_sidecar_json(state, "artifacts/request.json", {"request": request}, kind="test",
                             schema_version="test.v1", created_by="test")
    writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={**identity,
        "schema_version": "task-pipeline-generation.v1", "task_ids": ["T1"]}))
    writer.append(ZfEvent(type="workflow.operation.requested", payload={**identity, "request_ref": ref}))
    writer.append(ZfEvent(type="task.pipeline.stage.dispatched", payload={**identity,
        "workdir": str(root), "source_branch": "worker-test"}))
    writer.append(ZfEvent(type="workflow.call.result.admitted", payload={**identity,
        "semantic_verdict": "blocked", "control_result_ref": {"source_event_id": "blocked"}}))
    writer.append(ZfEvent(type="workflow.operation.settled", payload=identity))
    (root / "probe.py").write_text("import os\nassert 'ZF_WEB_ACTION_TOKEN' not in os.environ\nprint('probe passed')\n")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "must-not-reach-probe")
    return service, root, identity


def approve(service, manifest):
    return apply(service, manifest, approval_ref="owner:review-1", reviewed_digest=manifest["manifest_digest"])


def test_real_git_and_probe_effects_preserve_task_verdict_and_are_idempotent(case):
    service, root, _ = case
    before = git(root, "rev-parse", "HEAD")
    manifest = preview(service, operation_id="op-1", kind="commit", paths=["probe.py"])
    receipt = approve(service, manifest)
    assert receipt["result"]["source_commit"] != before
    assert git(root, "status", "--porcelain") == ""
    assert approve(service, manifest)["status"] == "already_recorded"
    check = preview(service, operation_id="op-1", kind="verify", command_id="contract-verification")
    result = approve(service, check)["result"]
    assert result["exit_code"] == 0 and result["source_stable"]
    assert result["execution_origin"] == "operator_authorized_host"
    assert result["product_verdict"] == "not_evaluated"
    assert approve(service, check)["status"] == "already_recorded"
    assert TaskStore(service.state_dir / "kanban.json").get("T1").status == "in_progress"
    assert not any(e.type in {"task.done", "dev.build.done", "test.passed"}
                   for e in service.writer.event_log.read_all())


@pytest.mark.parametrize("change", ["source", "digest", "staged", "approval", "holder", "generation", "contract", "paused"])
def test_changed_or_unauthorized_manifest_cannot_commit(case, monkeypatch, change):
    service, root, identity = case
    manifest = preview(service, operation_id="op-1", kind="commit", paths=["probe.py"])
    before = git(root, "rev-parse", "HEAD")
    if change == "source": (root / "probe.py").write_text("print('changed')\n")
    if change == "digest": manifest["head"] = "0" * 40
    if change == "staged": subprocess.run(["git", "-C", str(root), "add", "--", "probe.py"], check=True)
    if change == "holder": monkeypatch.setattr("zf.runtime.environment_exec.resource_holders",
                                               lambda plan: [{"pid": 123}])
    if change == "generation": service.writer.append(ZfEvent(type="workflow.operation.requested", payload={
        **identity, "operation_id": "op-2", "operation_generation": 2}))
    if change == "contract": TaskStore(service.state_dir / "kanban.json").update("T1", contract=TaskContract(acceptance="new"))
    if change == "paused": service.writer.emit("run.paused", payload={"workflow_run_id": "run-1"})
    with pytest.raises((ValueError, OSError)):
        apply(service, manifest, approval_ref="" if change == "approval" else "owner:review",
              reviewed_digest=manifest["manifest_digest"])
    assert git(root, "rev-parse", "HEAD") == before


def test_unresolved_started_effect_is_never_reexecuted(case):
    service, _, _ = case
    manifest = preview(service, operation_id="op-1", kind="commit", paths=["probe.py"])
    service.writer.emit("runtime.action.attempt.started", actor="operator", payload={
        "action": "task-host-assist", "manifest_digest": manifest["manifest_digest"]})
    with pytest.raises(ValueError, match="unresolved"):
        approve(service, manifest)


@pytest.mark.parametrize("name", ["../outside", ".git/config", "other.py"])
def test_outside_paths_rejected(case, name):
    service, _, _ = case
    with pytest.raises(ValueError):
        preview(service, operation_id="op-1", kind="commit", paths=[name])


def test_live_operation_cannot_get_host_assistance(case):
    service, _, identity = case
    service.writer.append(ZfEvent(type="workflow.operation.requested", payload={
        **identity, "operation_id": "op-live", "operation_generation": 2}))
    with pytest.raises(ValueError, match="settled"):
        binding(service, "op-live")


def test_dependency_change_invalidates_review_before_effect(case):
    service, root, _ = case
    dependency = root / "local-dependency"
    dependency.mkdir()
    (dependency / "compiler.py").write_text("compiler v1")
    # Keep this declared environment out of the Git delta like a provisioned cache.
    exclude = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
    exclude.write_text(exclude.read_text() + "\nlocal-dependency/\n")
    service.config.runtime.workdirs.required_paths = ["local-dependency"]
    manifest = preview(service, operation_id="op-1", kind="commit", paths=["probe.py"])
    (dependency / "compiler.py").write_text("compiler v2")
    with pytest.raises(ValueError, match="stale"):
        approve(service, manifest)


def test_nonzero_probe_is_recorded_without_retry_or_product_success(case):
    service, root, _ = case
    (root / "probe.py").write_text("raise SystemExit(7)\n")
    approve(service, preview(service, operation_id="op-1", kind="commit", paths=["probe.py"]))
    manifest = preview(service, operation_id="op-1", kind="verify", command_id="contract-verification")
    result = approve(service, manifest)["result"]
    assert result["status"] == "failed" and result["exit_code"] == 7
    assert approve(service, manifest)["status"] == "already_recorded"


def test_cli_preview_is_read_only_and_has_no_apply_switch(case, monkeypatch, capsys):
    import argparse
    from zf.cli.recover_host_assist import register
    service, _, _ = case
    monkeypatch.setattr("zf.cli.recover_host_assist.resolve_project_context", lambda **kwargs:
        SimpleNamespace(state_dir=service.state_dir, config=None, project_root=service.project_root))
    # Reuse the real bound service so this also exercises currentness checks.
    monkeypatch.setattr("zf.cli.recover_host_assist.ControlledActionService", lambda *a, **k: service)
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    argv = ["host-assist", "--operation", "op-1", "--kind", "commit", "--path", "probe.py"]
    before = service.writer.event_log.path.read_bytes()
    args = parser.parse_args(argv)
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "commit"
    assert service.writer.event_log.path.read_bytes() == before
    with pytest.raises(SystemExit):
        parser.parse_args([*argv, "--apply"])


def test_web_apply_requires_explicit_token_even_with_trusted_session(tmp_path, monkeypatch):
    from tests.test_web_workflow_proposals import _client, _action_body
    client, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("ZF_WEB_TRUSTED_SESSION", "1")
    route = "/api/projects/default/actions/task-host-assist"
    body = _action_body("host-assist-auth", {"manifest": {}, "approval_ref": "owner", "reviewed_digest": "x"})
    assert client.post(route, json=body).status_code == 403
    admitted = client.post(route, headers={"X-ZF-Web-Token": "test-token"}, json=body)
    assert admitted.status_code == 409, admitted.text
    assert "digest" in admitted.json()["reason"]


def test_web_token_gate_reaches_real_controlled_commit(case):
    from fastapi.testclient import TestClient
    from tests.test_web_workflow_proposals import _action_body
    from zf.core.config.loader import load_config
    from zf.web.server import create_app

    service, root, _ = case
    path = service.project_root / "test-config.yaml"
    path.write_text('version: "1.0"\nproject: {name: host-test, state_dir: .state}\nroles: []\n')
    config = load_config(path)
    config.runtime.workdirs.required_paths = []
    config.runtime.workdirs.provision_paths = []
    client = TestClient(create_app(service.state_dir, config=config, project_root=service.project_root))
    manifest = preview(service, operation_id="op-1", kind="commit", paths=["probe.py"])
    response = client.post("/api/projects/default/actions/task-host-assist",
        headers={"X-ZF-Web-Token": "must-not-reach-probe"},
        json=_action_body("reviewed-task-commit", {"manifest": manifest,
            "approval_ref": "owner:review-1", "reviewed_digest": manifest["manifest_digest"]}))
    assert response.status_code == 200, response.text
    assert response.json()["result"]["source_commit"] == git(root, "rev-parse", "HEAD")
