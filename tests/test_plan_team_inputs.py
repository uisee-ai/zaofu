"""Exercise the actual fanout → call manifest → workdir file used by each Plan member."""
import json

import pytest

from tests.test_prd_stage_teams import select
from tests.test_reader_fanout_runtime import _state
from tests.test_workflow_registered_controller import _candidate, assembly
from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig
from zf.runtime.artifact_workdir_view import AttemptFiles
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.result_submit import ResultSubmitError, SemanticResultSubmitService, provision_role_submit_credential


def _team(assembly, tmp_path, refs):
    roles = list(refs)
    plan = select(assembly, roles)
    for member in plan["members"]:
        member.update(input_refs=[refs[member["role"]]], selection_reason="Review a separate risk.",
                      completion_condition="Return a finding with its source.")
    # The checked proposal and the later dispatch deliberately live in separate fixtures.
    (assembly[0].parent / "prd.md").write_text("# Authentication\n# Test Accounts\n# Auth\n")
    config = load_config(_candidate(assembly)[2])
    root = tmp_path / "run"
    root.mkdir()
    state, log, transport, runtime = _state(root)
    runtime.config.workflow.stages = [s for s in config.workflow.stages if s.id in {"prd-scan", "prd-plan"}]
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader")
                            for r in [*roles, "planner", "plan-critic"]]
    runtime.config.roles.extend(r for r in config.roles if r.role_kind == "writer")
    for role in runtime.config.roles:
        provision_role_submit_credential(state, role.instance_id)
    return root, state, log, transport, runtime


def _files(state, event):
    payload = event.payload["payload"]
    manifest = hydrate_sidecar_ref(state, payload["attempt_source_manifest"]).payload
    role = event.payload["role_instance"]
    return AttemptFiles(state, manifest, {"actor": role, "role": role, "purpose": manifest["read_purpose"],
                                          "operation_id": payload["operation_id"]},
                        payload["required_reads"], lambda: {})


def read_plan_member_inputs(runtime, dispatch):
    """Deliver the actor's real 212 input files through the native mount adapter."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from uuid import uuid4

    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime import artifact_mount
    from zf.runtime.artifact_mount import MountAccess

    d = dispatch.payload
    view = _files(runtime.state_dir, dispatch)
    view.identity["provider"] = "codex"
    role = d["role_instance"]
    sid = str(uuid4())
    transcript = runtime.state_dir / f"{sid}.jsonl"
    mount = runtime.state_dir / "test-input-mount"
    access = MountAccess(runtime.state_dir, view, mount, "/worker-lane", lambda: 1, "0.171.0")

    def put(row):
        with transcript.open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    put({"type": "session_meta", "payload": {"id": sid}})
    RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", runtime.project_root).bind_codex_session(
        role, sid, session_path=transcript)
    with patch.object(artifact_mount, "peer_identity", return_value="mock-peer"):
        for i, name in enumerate(view.files):
            path = str(mount / name)
            call_id = f"plan-input-{i}"
            call = {"type": "response_item", "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "function_call", "name": "exec_command", "call_id": call_id,
                            "arguments": json.dumps({"cmd": "/usr/bin/cat " + path})}}
            put(call)
            with patch.object(artifact_mount, "open_call",
                    return_value={"session_id": sid, "call": call, "actual_path": path, "peer_pid": 1}):
                shown = access.read(access.open(name, 1), 0, 65536).decode()
            put({"type": "response_item", "timestamp": datetime.now(timezone.utc).isoformat(),
                 "payload": {"type": "function_call_output", "call_id": call_id,
                             "output": "Chunk ID: 0001\nWall time: 0.01 seconds\nProcess exited with code 0\nOutput:\n" + shown}})


def submit_plan_expert(runtime, dispatch, report):
    p = dispatch.payload
    role = p["role_instance"]
    token = provision_role_submit_credential(runtime.state_dir, role).read_text().strip()
    read_plan_member_inputs(runtime, dispatch)
    service = SemanticResultSubmitService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    result = service.submit(operation_id=p["operation_id"], semantic_result=report, role_instance=role, credential=token)
    admitted = next(e for e in runtime.event_log.read_all() if e.id == result.canonical_event_id)
    runtime.run_once(events=[admitted])
    return admitted


def test_each_expert_reads_its_section_and_planner_opens_every_admitted_report(assembly, tmp_path):
    root, state, log, transport, runtime = _team(assembly, tmp_path, {
        "product-scan": "prd.md#authentication", "tech-scan": "prd.md#test-accounts"})
    path = root / "prd.md"
    path.write_text("# Authentication\nIndependent sign-in.\n## Reconnect\nRecover the ticket.\n"
                    "# Test Accounts\nUse local fixtures.\n# Out of scope\nPrivate appendix.\n")
    writer = runtime.event_writer
    entry = writer.emit("prd.requested", correlation_id="run",
                        payload={"workflow_run_id": "run", "flow_kind": "prd", "pdd_id": "F", "prd_ref": "prd.md"})
    runtime.run_once(events=[entry])
    dispatches = log.query(type="fanout.child.dispatched")
    assert len(dispatches) == 2
    reports = {}
    for e in dispatches:
        view = _files(state, e)
        files = list(view.files)
        shown = "\n".join(view.read(p)[0].decode() for p in files)
        role = e.payload["role_instance"]
        assert "Private appendix" not in shown
        assert ("Independent sign-in" in shown) == (role == "product-scan")
        assert ("Recover the ticket" in shown) == (role == "product-scan")
        assert ("Use local fixtures" in shown) == (role == "tech-scan")
        assert {r["source_id"] for r in e.payload["payload"]["required_reads"]}.intersection(
            s["source_id"] for _, s in view.files.values())
        briefing = next(b for r, b, *_ in transport.sent if r == role).read_text()
        assert "Review a separate risk." in briefing and "Return a finding with its source." in briefing
        body = {"child_id": role, "status": "passed", "summary": f"Independent findings of {role}.",
                "findings": [], "recommendation": "approve"}
        reports[role] = body
        token = provision_role_submit_credential(state, role).read_text().strip()
        with pytest.raises(ResultSubmitError, match="read|delivery"):
            SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=writer).submit(
                operation_id=e.payload["operation_id"], semantic_result=body, role_instance=role, credential=token)
        submit_plan_expert(runtime, e, body)
    handoff, = log.query(type="prd.scan.completed")
    runtime.run_once(events=[handoff])
    planner = log.query(type="fanout.child.dispatched")[-1]
    assert planner.payload["role_instance"] == "planner"
    view = _files(state, planner)
    delivered = {r["source_id"]: json.loads(view.read(p)[0]) for p, (r, _) in view.files.items()
                 if r["source_id"].startswith("plan-report-")}
    assert {v["summary"] for v in delivered.values()} == {b["summary"] for b in reports.values()}
    required = {r["source_id"] for r in planner.payload["payload"]["required_reads"]}
    assert set(delivered) <= required
    from tests.test_result_submit import _plan_candidate

    service = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=writer)
    token = provision_role_submit_credential(state, "planner").read_text().strip()
    request = {"operation_id": planner.payload["operation_id"], "semantic_result": _plan_candidate(),
               "role_instance": "planner", "credential": token}
    check = service.validate_plan_candidate(**request, project_root=root)
    assert check["status"] == "failed"
    assert {e["code"] for e in check["errors"]} == {"required_read_missing"}
    with pytest.raises(ResultSubmitError) as unread:
        service.submit(**request)
    assert "read" in str(unread.value).lower() or "delivery" in str(unread.value).lower(), str(unread.value)
    read_plan_member_inputs(runtime, planner)
    check = service.validate_plan_candidate(**request, project_root=root)
    assert check["status"] == "passed", json.dumps(check["errors"])
    result = service.submit(**request)
    assert result.canonical_event_type == "prd.plan.child.completed"
    path.write_text("Later unrelated amendment.")
    specialist = next(e for e in dispatches if e.payload["role_instance"] == "product-scan")
    restored_view = _files(state, specialist)
    assert "Independent sign-in" in "\n".join(restored_view.read(p)[0].decode() for p in restored_view.files)


@pytest.mark.parametrize("problem", ["missing", "ambiguous", "foreign_symlink", "restricted", "acl_alias", "stale"])
def test_missing_or_unauthorized_member_input_has_no_dispatch(assembly, tmp_path, problem):
    root, _, log, transport, runtime = _team(assembly, tmp_path, {"tech-scan": "prd.md#auth"})
    target = root / "prd.md"
    target.write_text("# Auth\nPermitted.\n" if problem != "missing" else "# Other\nMissing.\n")
    if problem == "ambiguous":
        target.write_text("# Auth\nOne.\n# Auth\nTwo.\n")
    if problem == "foreign_symlink":
        outside = tmp_path / "not-a-project-input.md"
        outside.write_text("# Auth\nOutside.\n")
        target.unlink()
        target.symlink_to(outside)
    descriptor = {"ref": "prd.md"}
    if problem == "acl_alias":
        alias = root / "alias.md"
        alias.hardlink_to(target)
        descriptor.update(ref="alias.md", access_scope={"actors": ["someone-else"]})
    if problem == "restricted":
        descriptor["access_scope"] = {"actors": ["someone-else"]}
    if problem == "stale":
        descriptor["sha256"] = "f" * 64
    entry = runtime.event_writer.emit("prd.requested", correlation_id="run", payload={
        "workflow_run_id": "run", "flow_kind": "prd", "pdd_id": "F", "prd_ref": "prd.md",
        "artifact_refs": [descriptor]})
    runtime.run_once(events=[entry])
    assert not transport.sent
    failure, = log.query(type="fanout.child.failed")
    assert "correct the source binding" in failure.payload["reason"]
    assert "check-tech-scan" in failure.payload["reason"]


def test_fresh_reader_retry_reuses_only_its_original_pinned_section(assembly, tmp_path):
    from tests.test_reader_fanout_runtime import _manifest
    from zf.runtime.artifact_input_index import load_attempt_source_manifest
    from zf.runtime.reader_fanout_retry import prepare_fresh_reader_retry

    root, state, log, _, runtime = _team(assembly, tmp_path, {"tech-scan": "prd.md#auth"})
    (root / "prd.md").write_text("# Auth\nOriginal requirement.\n")
    entry = runtime.event_writer.emit("prd.requested", correlation_id="run", payload={
        "workflow_run_id": "run", "flow_kind": "prd", "pdd_id": "F", "prd_ref": "prd.md"})
    runtime.run_once(events=[entry])
    first, = log.query(type="fanout.child.dispatched")
    manifest = _manifest(state, first.payload["fanout_id"])
    from zf.runtime.call_result_runtime import workflow_operation_service

    workflow_operation_service(runtime).fail(operation_id=first.payload["operation_id"],
        request_hash=first.payload["request_hash"], workflow_run_id="run", reason="provider disconnected")
    from zf.core.events.model import ZfEvent

    idle = runtime.event_writer.append(ZfEvent(type="worker.state.changed", actor="tech-scan", origin="kernel",
        correlation_id="run", payload={"to": "idle", "instance_id": "tech-scan"}))
    runtime.run_once(events=[idle])
    (root / "prd.md").write_text("# Auth\nUnapproved change.\n")
    retry = prepare_fresh_reader_retry(runtime, manifest=manifest, child=manifest["children"][0],
        role=next(r for r in runtime.config.roles if r.instance_id == "tech-scan"), attempt=1, previous_dispatch=first)
    current = load_attempt_source_manifest(state, retry.attempt_id)
    earlier = _files(state, first).manifest
    source, = [s for s in current["sources"] if s["source_id"].startswith("plan-input-")]
    previous, = [s for s in earlier["sources"] if s["source_id"].startswith("plan-input-")]
    assert source["sha256"] == previous["sha256"] and source["ref"] == previous["ref"]
    assert current["attempt_id"] != earlier["attempt_id"]

    from types import SimpleNamespace
    from zf.runtime.artifact_read_ledger import ArtifactReadError
    from zf.runtime.plan_team_inputs import bind_plan_team_inputs

    child = SimpleNamespace(child_id=first.payload["child_id"], payload=dict(first.payload["payload"]))
    child.payload["workflow_run_id"] = "other"
    with pytest.raises(ArtifactReadError, match="another assignment"):
        bind_plan_team_inputs(runtime, child, "prd-scan", "other", predecessor=first.payload["operation_id"])
    child.payload["workflow_run_id"] = "run"
    (state / previous["ref"]).unlink()
    with pytest.raises(ArtifactReadError, match="lost its pinned snapshot"):
        bind_plan_team_inputs(runtime, child, "prd-scan", "run", predecessor=first.payload["operation_id"])


def test_undeclared_plan_team_keeps_original_shadow_dispatch(assembly, tmp_path):
    config = load_config(_candidate(assembly)[2])
    root = tmp_path / "legacy"
    root.mkdir()
    _, log, transport, runtime = _state(root)
    scan = next(s for s in config.workflow.stages if s.id == "prd-scan")
    runtime.config.workflow.stages = [scan]
    runtime.config.roles = [RoleConfig(name=r, instance_id=r, backend="mock", role_kind="reader") for r in scan.roles]
    entry = runtime.event_writer.emit("prd.requested", correlation_id="old-run", payload={
        "workflow_run_id": "old-run", "flow_kind": "prd", "pdd_id": "F", "prd_ref": "legacy-missing.md"})
    runtime.run_once(events=[entry])
    assert {row[0] for row in transport.sent} == set(scan.roles)
    for dispatch in log.query(type="fanout.child.dispatched"):
        payload = dispatch.payload["payload"]
        assert "stage_obligation" not in payload and payload.get("result_protocol_mode") != "blocking"


@pytest.mark.parametrize("problem", ["valid", "digest", "self_declared", "foreign_revision", "wrong_path", "restricted"])
def test_plan_author_reads_only_its_admitted_workflow_requirement(assembly, tmp_path, problem):
    from pathlib import Path
    from zf.core.events.model import ZfEvent
    from zf.runtime.workflow_requests import _write_requirement_spec

    _, state, log, transport, runtime = _team(assembly, tmp_path, {})
    request_id = "prepare-TASK-1-contract"
    spec = {"schema_version": "requirement.v1", "request_id": request_id, "revision": 2,
            "confirmed": True, "objective": "Return exactly hello."}
    ref, digest = _write_requirement_spec(state, spec)
    if problem == "wrong_path":
        copy = state / "workflow-requests" / "unrelated.json"
        copy.write_bytes(Path(ref).read_bytes())
        ref = str(copy)
    binding = {"workflow_run_id": request_id, "request_id": request_id, "request_revision": 2,
               "requirement_spec_ref": ref, "requirement_spec_digest": digest,
               "artifact_refs": [{"path": ref}]}
    runtime.event_writer.append(ZfEvent(type="workflow.invoke.accepted", origin="kernel", correlation_id=request_id,
        payload={**binding, "workflow_run_id": "other" if problem == "self_declared" else request_id}))
    if problem == "digest":
        Path(ref).write_text('{"new":"unapproved"}')
    elif problem == "foreign_revision":
        binding["request_revision"] = 1
    elif problem == "restricted":
        binding["artifact_refs"] = [{"path": ref, "access_scope": {"roles": ["other"]}}]
    trigger = runtime.event_writer.append(ZfEvent(type="prd.requested", origin="kernel", correlation_id=request_id,
        payload={**binding, "flow_kind": "prd", "pdd_id": "F-plan"}))
    runtime.run_once(events=[trigger])
    if problem != "valid":
        assert not transport.sent
        failure, = log.query(type="fanout.child.failed")
        assert "Plan obligation plan-author" in failure.payload["reason"]
        return
    dispatched, = log.query(type="fanout.child.dispatched")
    view = _files(state, dispatched)
    sources = [(name, row) for name, (row, _) in view.files.items() if row["source_id"].startswith("plan-input-")]
    (name, row), = sources
    assert row["artifact_sha256"] == digest
    assert json.loads(view.read(name)[0]) == spec


def test_preflight_preserves_bound_labels_and_checks_permissions_before_sections(tmp_path):
    from zf.runtime.plan_team_inputs import validate_plan_project_sources
    from tests.test_prd_stage_teams import member

    role = member("tech-scan")
    role["input_refs"] = ["requirement", "label#auth", "future-report"]
    plan = {"members": [role]}
    (tmp_path / "label").mkdir()  # The descriptor takes precedence over a same-named project directory.
    file = tmp_path / "local.md"
    file.write_text("# Auth\nAllowed.\n")
    state = tmp_path / "configured-state"
    requirement = {"artifact_refs": [{"source_id": "label", "path": "local.md"}]}
    validate_plan_project_sources(plan, tmp_path, state, requirement)
    alias = tmp_path / "alias"
    alias.hardlink_to(file)
    requirement["artifact_refs"].append({"ref": "alias", "access_scope": {"roles": ["someone-else"]}})
    file.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="not authorized for this member"):
        validate_plan_project_sources(plan, tmp_path, state, requirement)
    assert not state.exists()
