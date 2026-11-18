from __future__ import annotations

import io
import json
import tarfile

import pytest

from tests.test_plan_artifact_package import _fixture_port, _package
from zf.cli.main import main
from zf.core.events import EventLog, ZfEvent
from zf.runtime.artifact_read_capability import bind_attempt_artifact_read_capability, provision_role_artifact_read_credential
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.plan_replan_authority_inputs import replan_authority_sources


@pytest.mark.parametrize("change", [
    "none", "wrong_role", "terminal", "no_credential", "run", "generation", "package",
    "acl", "selection", "descriptor", "bytes", "missing",
    "workspace_ok", "workspace_cwd", "workspace_symlink", "workspace_partial",
])
def test_planner_drafts_exact_bytes_or_emits_no_archive(tmp_path, monkeypatch, capsysbinary, change):
    state = tmp_path / "custom-state"
    package = _package(state)
    matrix = _fixture_port(state, "test_matrix", "换行\n")
    package["produced"].append(matrix)
    desc = write_plan_artifact_package(state, package)
    metadata = dict(workflow_run_id="run-1", task_map_generation=package["task_map_generation"],
                    plan_artifact_package_ref=desc["ref"], plan_artifact_package_digest=desc["sha256"])
    sources = replan_authority_sources(state_dir=state, project_root=tmp_path, payload=metadata, context={})
    selected = next(s for s in sources if s["source_id"] == "baseline-plan-port-test_matrix")
    if change == "acl":
        selected["access_scope"] = {"roles": ["private-role"]}
    elif change == "selection":
        selected["allowed_paths"] = ["$.value"]
    elif change == "descriptor":
        selected["ref"] = next(s["ref"] for s in sources if s["source_id"] == "baseline-plan-port-task_map")
    elif change == "bytes":
        (state / matrix["ref"]).write_text("{}")
    elif change == "package":
        metadata["plan_artifact_package_digest"] = "0" * 64
    elif change == "generation":
        metadata["task_map_generation"] = "different"
    manifest = build_attempt_source_manifest(
        workflow_run_id="another" if change == "run" else "run-1", task_id="T1", attempt_id="a1",
        dispatch_id="a1", sources=sources, metadata=metadata,
    )
    write_attempt_source_manifest(state, manifest)
    role = "critic" if change == "wrong_role" else "planner"
    path = provision_role_artifact_read_credential(state, role, role_name=role, provider="codex")
    bind_attempt_artifact_read_capability(state, operation_id="op1", attempt_id="a1", role_instance=role, manifest=manifest)
    monkeypatch.delenv("ZF_ARTIFACT_READ_TOKEN", raising=False)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", role)
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", "" if change == "no_credential" else str(path))
    log = EventLog(state / "events.jsonl")
    facts = dict(workflow_run_id=manifest["workflow_run_id"], operation_id="op1", active_attempt_id="a1")
    log.append(ZfEvent(type="workflow.operation.requested", actor="kernel", task_id="T1", payload=facts))
    if change == "terminal":
        log.append(ZfEvent(type="workflow.operation.settled", actor="kernel", task_id="T1", payload=facts))
    log.close()

    workspace = change.startswith("workspace_")
    if workspace:
        from zf.core.safety.path_guard import write_workdir_owner_marker
        (tmp_path / "zf.yaml").write_text(
            'version: "1.0"\nproject: {name: demo, state_dir: custom-state}\n'
            'roles: [{name: planner, backend: mock}]\n'
            'runtime: {workdirs: {enabled: true, root: custom-state/roles, mode: dry-run}}\n'
        )
        role_dir = state / "roles" / role
        write_workdir_owner_marker(role_dir, project_name="demo", instance_id=role, project_root=tmp_path)
        project = role_dir / "project"
        project.mkdir()
        monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
        monkeypatch.chdir(tmp_path if change == "workspace_cwd" else project)
        if change == "workspace_symlink":
            (project / "artifacts").symlink_to(tmp_path)
    args = ["plan", "--state-dir", str(state), "draft-baseline", "--attempt", "a1", "--port", "task_map",
                   "--port", "source_index" if change in ("missing", "workspace_partial") else "test_matrix", "--port", "task_map"]
    status = main([*args, "--workspace"] if workspace else args)
    stdout, stderr = capsysbinary.readouterr()
    if change == "none":
        assert status == 0, stderr
        with tarfile.open(fileobj=io.BytesIO(stdout)) as files:
            assert files.getnames() == ["task_map.json", "test_matrix.json"]
            for name in files.getnames():
                port = next(p for p in package["produced"] if p["logical_name"] == name[:-5])
                assert files.extractfile(name).read() == (state / port["ref"]).read_bytes()
    elif change == "workspace_ok":
        assert status == 0, stderr
        output = json.loads(stdout)
        path = project / output["draft_dir"] / "test_matrix.json"
        assert path.read_bytes() == (state / matrix["ref"]).read_bytes()
        path.write_text("edited")
        assert main([*args, "--workspace"]) == 0
        retry = json.loads(capsysbinary.readouterr().out)
        assert retry["draft_dir"] != output["draft_dir"] and path.read_text() == "edited"
    else:
        assert status == 1 and stderr and stdout == b""
        if workspace:
            assert not list(project.glob("artifacts/plan/replan-*"))
    assert not list((state / "artifacts/attempts/a1").glob("read-ledger*.jsonl"))
