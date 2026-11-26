from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.plan_rework_draft import KEY, bind_authoring_context, editable_candidate_files
from zf.runtime.workflow_operation import WorkflowOperationService


@pytest.fixture
def rework(tmp_path):
    state = tmp_path / "state"
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log, default_origin="kernel")
    binder = SimpleNamespace(state_dir=state, event_log=log)
    run, stage, generation = "invoice-run", "invoice-design", "scope-v3"
    formal = {
        "stage_replan_generation": generation,
        "task_map_generation": "accepted-v2",
        "plan_artifact_package_digest": "a" * 64,
    }

    def emit(kind, body, cause=None):
        return writer.append(
            ZfEvent(
                type=kind,
                origin="kernel",
                actor="zf-cli",
                correlation_id=run,
                causation_id=cause,
                payload={"workflow_run_id": run, "stage_id": stage, **body},
            )
        )

    def pin(value, root):
        return write_immutable_json_sidecar(
            state, value, root=root, kind="fixture", schema_version="fixture.v1", created_by="test"
        )

    old = emit("invoice.sources.ready", formal)
    old_start = emit("fanout.started", {"fanout_id": "invoices-r3", "trigger_event_id": old.id}, old.id)
    tid, op, att = "invoice-author", "op-a", "attempt-a"
    identity = {
        "workflow_run_id": run,
        "stage_id": stage,
        "fanout_id": "invoices-r3",
        "child_id": tid,
        "operation_id": op,
        "attempt_id": att,
        "request_hash": "f" * 64,
    }
    workflow = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    workflow.ensure_operation(
        workflow_run_id=run,
        operation_id=op,
        operation_type="fanout_reader_child",
        request=identity,
        active_attempt_id=att,
        role_instance="planner",
        causation_id=old_start.id,
    )
    identity["request_hash"] = next(
        e.payload["request_hash"]
        for e in log.read_all()
        if e.type == "workflow.operation.requested" and e.payload["operation_id"] == op
    )
    physical = {
        "task_map": ("artifacts/invoice-map.json", b'{"invoices": "keep accepted fix"}'),
        "plan.md": ("docs/invoice.md", b"# Invoice audit\n"),
    }
    refs = []
    for name, (relative, raw) in physical.items():
        sha = hashlib.sha256(raw).hexdigest()
        path = f"artifacts/fanouts/invoices-r3/{tid}/{relative}"
        target = state / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        refs.append(
            {
                "source_id": "child-artifact-" + name,
                "kind": "fanout_child_artifact",
                "ref": path,
                "sha256": sha,
                "allowed_paths": ["$"],
                "access_scope": {"roles": ["planner"]},
            }
        )
    ctrl = {
        **identity,
        "plan_ports": [
            {"logical_name": "task_map", "ref": physical["task_map"][0], "sha256": refs[0]["sha256"]}
        ],
        "plan_md": "# Invoice audit",
        "plan_artifact_ref": physical["plan.md"][0],
    }
    control = pin(ctrl, "control")
    refs.append(
        {**control, "kind": "call_control_result", "source_id": "child-control", "allowed_paths": ["$"]}
    )
    sem = emit("invoice.author.completed", identity)
    adm = emit(
        "workflow.call.result.admitted",
        {
            **identity,
            "control_result_ref": control,
            "admission_status": "admitted",
            "source_event_id": sem.id,
        },
        sem.id,
    )
    child = emit(
        "fanout.child.completed",
        {**identity, "result_event_id": sem.id, "control_result_ref": control, "report": ctrl},
        sem.id,
    )
    pf = pin(
        {
            "schema_version": "plan-candidate-preflight.v1",
            "status": "failed",
            "fanout_id": "invoices-r3",
            "stage_id": stage,
        },
        "preflights",
    )
    pf.update(kind="plan_candidate_preflight", source_event_id=old.id)
    failed = {
        "failure_class": "plan_candidate_preflight",
        "status": "failed",
        "fanout_id": "invoices-r3",
        "previous_plan_candidate_refs": refs,
        "plan_candidate_preflight_ref": pf,
    }
    synth = emit("fanout.synth.completed", failed, old.id)
    fail = emit("invoice.plan.failed", {**failed, **formal}, synth.id)
    next_stage = emit(
        "invoice.sources.ready",
        {**formal, "previous_plan_candidate_refs": refs, "rework_of": fail.id},
        fail.id,
    )
    start = emit(
        "fanout.started",
        {
            "fanout_id": "invoices-r4",
            "trigger_event_id": next_stage.id,
            "aggregate": {"failure_event": "invoice.plan.failed"},
            "expected_children": [{"child_id": "invoice-editor", "role_instance": "planner"}],
        },
        next_stage.id,
    )
    manifest = {
        "attempt_id": "attempt-b",
        "workflow_run_id": run,
        "source_event_id": start.id,
        "plan_artifact_package_digest": formal["plan_artifact_package_digest"],
        "task_map_generation": "accepted-v2",
        "sources": [{**r, "source_id": "previous-plan-candidate-" + str(i)} for i, r in enumerate(refs, 1)],
    }
    request = {
        "workflow_run_id": run,
        "stage_id": stage,
        "fanout_id": "invoices-r4",
        "active_attempt_id": "attempt-b",
        "role_instance": "planner",
        "child_id": "invoice-editor",
        "operation_type": "fanout_reader_child",
        "attempt_domain": "plan",
        "source_manifest_digest": canonical_json_sha256(manifest),
    }
    payload = {"plan_candidate_validation": {"manifest": {"trigger_payload": next_stage.payload}}}
    return binder, writer, request, manifest, payload, physical, adm, child


def issue(fix):
    rt, writer, request, manifest, payload, *_ = fix
    request["source_manifest_digest"] = canonical_json_sha256(manifest)
    bind_authoring_context(rt, payload, request, manifest, None, "op-b")
    WorkflowOperationService(
        state_dir=rt.state_dir, event_log=rt.event_log, event_writer=writer
    ).ensure_operation(
        workflow_run_id=request["workflow_run_id"],
        operation_id="op-b",
        operation_type=request["operation_type"],
        request=request,
        active_attempt_id=request["active_attempt_id"],
        role_instance=request["role_instance"],
    )
    return request[KEY]


def export(fix, **changes):
    rt, _, _, manifest, *_ = fix
    identity = {
        "operation_id": "op-b",
        "role": "planner",
        "actor": "planner",
        "purpose": "workflow",
        **changes,
    }
    return editable_candidate_files(rt.state_dir, rt.event_log, manifest, identity, ["task_map"])


def test_signed_edit_grant_preserves_candidate_and_distinct_approved_baseline(rework):
    rt, _, req, manifest, payload, physical, admission, _ = rework
    legacy = deepcopy(req)
    manifest_sha = canonical_json_sha256(manifest)
    grant = issue(rework)
    assert "ref" in grant and manifest_sha == canonical_json_sha256(manifest)
    files, origin = export(rework)
    assert files == {"task_map.json": physical["task_map"][1], "plan.md": physical["plan.md"][1]}
    assert origin["producer_admission_event_id"] == admission.id
    assert origin["authority_package_digest"] == "a" * 64
    assert (
        origin["plan_file_sha256"] != origin["plan_inline_sha256"] and origin["single_trailing_lf_difference"]
    )
    for previous, expected in ((req, grant), (legacy, None)):
        current = deepcopy(legacy)
        dispatch = deepcopy(payload)
        dispatch[KEY] = {"untrusted": True}
        bind_authoring_context(rt, dispatch, current, manifest, previous, "op-b")
        assert current.get(KEY) == expected and dispatch.get(KEY) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        "forged_partition",
        "wrong_run",
        "wrong_generation",
        "partial_root",
        "wrong_acl",
        "unassigned_port",
        "cross_bundle",
        "duplicate_port",
        "wrong_preflight",
        "mutated_file",
        "wrong_start",
        "truncated_plan",
    ],
)
def test_invalid_or_incomplete_rework_never_silently_seeds_approved_plan(rework, invalid):
    rt, _, req, manifest, payload, physical, _, _ = rework
    scope = payload["plan_candidate_validation"]["manifest"]["trigger_payload"]
    if invalid == "forged_partition":
        manifest["sources"][0].update(
            kind="plan_rework_candidate_partition",
            allowed_paths=["$.private"],
            access_scope={"read_equivalents": []},
        )
    if invalid == "wrong_run":
        req["workflow_run_id"] = "other"
    if invalid == "wrong_generation":
        scope["stage_replan_generation"] = "other"
    if invalid == "partial_root":
        scope["previous_plan_candidate_refs"][0]["allowed_paths"] = ["$.invoices"]
    if invalid == "wrong_acl":
        manifest["sources"][0]["access_scope"]["roles"] = ["other"]
    if invalid == "unassigned_port":
        manifest["sources"].pop(0)
    if invalid == "cross_bundle":
        manifest["sources"][0]["ref"] = "artifacts/fanouts/other/file.json"
    if invalid == "duplicate_port":
        manifest["sources"].append(dict(manifest["sources"][0]))
    if invalid == "wrong_preflight":
        scope["rework_of"] = "missing"
    if invalid == "mutated_file":
        (rt.state_dir / manifest["sources"][0]["ref"]).write_bytes(b"changed")
    if invalid == "wrong_start":
        manifest["source_event_id"] = "stale-fanout"
    if invalid == "truncated_plan":
        (rt.state_dir / manifest["sources"][1]["ref"]).write_bytes(b"# Invoice")
    assert issue(rework).get("unavailable")
    with pytest.raises(ValueError, match="no complete editable"):
        export(rework)


def test_bound_actor_manifest_and_granted_bytes_are_checked_on_each_export(rework):
    rt, _, _, manifest, *_ = rework
    issue(rework)
    with pytest.raises(ValueError, match="current Planner"):
        export(rework, role="critic")
    with pytest.raises(ValueError, match="identity mismatch"):
        export(rework, actor="other-planner")
    (rt.state_dir / manifest["sources"][0]["ref"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="bytes differ"):
        export(rework)
    manifest["sources"][0]["sha256"] = "bad"
    with pytest.raises(ValueError, match="manifest differs"):
        export(rework)


def test_public_cli_and_briefing_export_immutable_candidate_from_owned_workspace(
    rework, tmp_path, monkeypatch, capsys
):
    import json
    from zf.cli.main import main
    from zf.core.safety.path_guard import write_workdir_owner_marker
    from zf.runtime.artifact_read_capability import (
        bind_attempt_artifact_read_capability,
        provision_role_artifact_read_credential,
    )
    from zf.runtime.artifact_read_ledger import write_attempt_source_manifest
    from zf.runtime.artifact_role_inputs import render_role_input_entry

    runtime, _, _, manifest, payload, physical, *_ = rework
    manifest["sources"].extend(
        {"source_id": name, "allowed_paths": ["$"]}
        for name in ("plan-rework-package", "baseline-plan-port-task_map")
    )
    manifest["task_id"] = "invoice-editor"
    descriptor = write_attempt_source_manifest(runtime.state_dir, manifest)
    issue(rework)
    active = {
        **payload,
        "attempt_id": "attempt-b",
        "operation_id": "op-b",
        "attempt_source_manifest_ref": descriptor["ref"],
        "attempt_source_manifest_sha256": descriptor["sha256"],
    }
    briefing = render_role_input_entry(active, runtime.state_dir, role_name="planner")
    assert "--from-rework" in briefing and "Goal authority" in briefing
    assert "--from-rework" not in render_role_input_entry(active, runtime.state_dir, role_name="critic")
    token = provision_role_artifact_read_credential(
        runtime.state_dir, "planner", role_name="planner", provider="codex"
    )
    bind_attempt_artifact_read_capability(
        runtime.state_dir,
        operation_id="op-b",
        attempt_id="attempt-b",
        role_instance="planner",
        manifest=manifest,
    )
    monkeypatch.delenv("ZF_ARTIFACT_READ_TOKEN", raising=False)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "planner")
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(token))
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject: {name: invoices, state_dir: state}\n'
        "roles: [{name: planner, backend: mock}]\n"
        "runtime: {workdirs: {enabled: true, root: state/roles, mode: dry-run}}\n"
    )
    role_dir = runtime.state_dir / "roles" / "planner"
    write_workdir_owner_marker(
        role_dir, project_name="invoices", instance_id="planner", project_root=tmp_path
    )
    workspace = role_dir / "project"
    workspace.mkdir()
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(workspace)
    args = [
        "plan",
        "--state-dir",
        str(runtime.state_dir),
        "draft-baseline",
        "--workspace",
        "--from-rework",
        "--attempt",
        "attempt-b",
        "--port",
        "task_map",
    ]
    code = main(args)
    output, stderr = capsys.readouterr()
    assert code == 0, stderr
    result = json.loads(output)
    exported = workspace / result["draft_dir"]
    assert (exported / "task_map.json").read_bytes() == physical["task_map"][1]
    assert (exported / "plan.md").read_bytes() == physical["plan.md"][1]
    assert result["file_sha256"]["plan.md"] == hashlib.sha256(physical["plan.md"][1]).hexdigest()
    assert main([*args, "--seed-goals"]) == 1
    assert main([*args, "--port", "execution_spec"]) == 1
    assert list(exported.parent.iterdir()) == [exported]
