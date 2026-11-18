"""An unavailable runner can be reported without asserting unread product claims."""
import json
import io
import shlex
from types import SimpleNamespace
import pytest

from tests.test_verification_submit_preflight import _running_verify
from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref, active_read_ledger_path
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.reader_submission_guidance import verifier_failure_schema_guidance


@pytest.mark.parametrize("profile", ["task-verify", "candidate-verify"])
def test_rendered_failure_example_validates_and_submits_without_success_template(tmp_path, profile):
    runtime, service, args, result = _unread_verifier(tmp_path, profile)
    guidance = verifier_failure_schema_guidance(
        owner=result["verification_owner"], tier=result["verification_tier"],
    )
    body = json.loads(guidance.split("```json\n", 1)[1].split("\n```", 1)[0])
    body.update(summary=result["summary"], evidence_refs=result["evidence_refs"])
    validated = service.validate_plan_candidate(**args, semantic_result=body)
    assert validated["status"] == "passed", validated
    submitted = service.submit(**args, semantic_result=body)
    assert submitted.canonical_event_type == "verify.child.failed"
    assert not any(e.type == "verify.child.completed" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("profile", ["task-verify", "candidate-verify"])
def test_rendered_stdin_failure_command_uses_real_cli_admission(tmp_path, profile, monkeypatch, capsys):
    from zf.cli.main import build_parser
    from zf.cli.hook_workdir_guard import bash_command_looks_mutating
    from zf.runtime.stage_execution_card import prepare_profiled_stage_result

    runtime, service, args, result = _unread_verifier(tmp_path, profile)
    (runtime.project_root / "zf.yaml").write_text(
        'version: "1.0"\nproject: {name: failure-stdin, state_dir: .zf}\nroles: []\n')
    monkeypatch.chdir(runtime.project_root)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", args["role_instance"])
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN", args["credential"])
    monkeypatch.delenv("ZF_RESULT_SUBMIT_TOKEN_FILE", raising=False)
    operation = load_workflow_operation(runtime.event_log, args["operation_id"])
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    _, lines = prepare_profiled_stage_result(
        state_dir=runtime.state_dir, child_payload={**request, "operation_id": args["operation_id"],
            "output_profile_id": profile, "output_profile_revision": "1"},
        success_payload={"verification_result": {**result, "product_acceptance_report": {}}},
        run_id="unread-verify", cli_command="zf",
    )
    rendered = "\n".join(lines).split("Execution-failure command:", 1)[1]
    command = rendered.split("```bash\n", 1)[1].split("\n```", 1)[0]
    head, body = command.split(" <<'ZF_EXECUTION_FAILURE'\n", 1)
    semantic = json.loads(body.rsplit("\nZF_EXECUTION_FAILURE", 1)[0])
    semantic.update(summary=result["summary"], evidence_refs=result["evidence_refs"])
    body = json.dumps(semantic)
    assert not bash_command_looks_mutating(head + " <<'ZF_EXECUTION_FAILURE'\n" + body + "\nZF_EXECUTION_FAILURE")
    scratch = runtime.state_dir / request["result_scratch_ref"]
    before = scratch.read_bytes()
    cli = build_parser().parse_args(shlex.split(head)[1:])
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(body.encode())))
    assert cli.func(cli) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "admitted"
    assert output["canonical_event_type"] == "verify.child.failed"
    assert scratch.read_bytes() == before
    assert not any(e.type == "verify.child.completed" for e in runtime.event_log.read_all())


def _unread_verifier(tmp_path, profile):
    runtime, original, service, credential, result = _running_verify(tmp_path, profile)
    operation = load_workflow_operation(runtime.event_log, original.operation_id)
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    identity = request["result_identity"]
    runtime.event_writer.emit("plan.artifact_package.admitted", payload={
        "workflow_run_id": result["workflow_run_id"],
        "task_map_generation": result["task_map_generation"],
        "plan_revision": "1", "plan_artifact_package_id": "package-1",
        "package_ref": identity["plan_artifact_package_ref"],
        "package_digest": identity["plan_artifact_package_digest"],
    })
    source = runtime.project_root / "unread.json"
    source.write_text('{"product": "requires independent evaluation"}')
    ref = materialize_attempt_source_ref(state_dir=runtime.state_dir,
        project_root=runtime.project_root, ref=str(source), source_id="unread-input")
    payload = {**request["result_identity"], **result,
        "role_instance": "verify-1", "run_id": "unread-verify",
        "output_profile_id": profile, "input_refs": [ref],
        "canonical_success_event": "verify.child.completed",
        "canonical_failure_event": "verify.child.failed"}
    prepared = prepare_call_operation(runtime, payload=payload,
        operation_type="fanout_reader_child", operation_key="unread-verify",
        stage_id="verify", task_id=result["task_id"], dispatch_id="unread-verify")
    mark_call_operation_started(runtime, prepared, task_id=result["task_id"],
        dispatch_id="unread-verify")
    evidence = runtime.project_root / "docker-probe.txt"
    evidence.write_text("connect /var/run/docker.sock: operation not permitted\n")
    result.update(execution_status="failed", verdict="abstained",
        failure_class="verifier_execution_failure", summary="Sandbox denied the Docker probe.",
        evidence_refs=[str(evidence)], requirement_results=[], probe_receipts=[],
        reused_command_receipt_ids=[])
    result.pop("product_acceptance_report", None)
    args = dict(operation_id=prepared.operation_id, role_instance="verify-1",
        credential=credential.read_text().strip())
    return runtime, service, args, result


@pytest.mark.parametrize("profile", ["task-verify", "candidate-verify"])
def test_unavailable_verifier_submits_abstention_before_product_reads(tmp_path, profile):
    runtime, service, args, result = _unread_verifier(tmp_path, profile)
    submitted = service.submit(**args, semantic_result=result)
    assert submitted.canonical_event_type == "verify.child.failed"
    events = runtime.event_log.read_all()
    admitted = next(e for e in events if e.type == "workflow.call.result.admitted")
    envelope = hydrate_sidecar_ref(runtime.state_dir, admitted.payload["envelope_ref"]).payload
    assert envelope["execution"]["status"] == "failed"
    assert envelope["input_consumption"]["status"] == "incomplete"
    assert not any(e.type == "verify.child.completed" for e in events)
    assert not (runtime.state_dir / "product-acceptance/reports").exists()


@pytest.mark.parametrize("fault", ["passed", "product_rejected", "no_evidence", "no_summary",
    "wrong_role", "wrong_target", "bad_ledger", "unperformed_report"])
def test_environment_abstention_preserves_admission_boundaries(tmp_path, fault):
    runtime, service, args, result = _unread_verifier(tmp_path, "candidate-verify")
    if fault == "passed": result.update(execution_status="completed", verdict="passed")
    elif fault == "product_rejected":
        result.update(execution_status="completed", verdict="rejected", failure_class="product_rejection")
    elif fault == "no_evidence": result["evidence_refs"] = []
    elif fault == "no_summary": result["summary"] = ""
    elif fault == "wrong_role": args["role_instance"] = "other-role"
    elif fault == "wrong_target":
        (runtime.state_dir / result["target_snapshot_ref"]).write_text('{"corrupt": true}')
    elif fault == "bad_ledger":
        path = active_read_ledger_path(runtime.state_dir, "unread-verify")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("corrupt ledger\n")
    elif fault == "unperformed_report": result["product_acceptance_report"] = {}
    with pytest.raises(ResultSubmitError):
        service.submit(**args, semantic_result=result)
    assert not any(e.type == "verify.child.completed" for e in runtime.event_log.read_all())
