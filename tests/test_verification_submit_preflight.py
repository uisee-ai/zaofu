"""Verifier schema repair must not consume the live submit capability."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from tests.test_product_acceptance import _package, _report, RUN_ID, GENERATION, TARGET, CANDIDATE
from tests.test_result_submit import _runtime
from tests.test_typed_result_admission import _contract, _verification_result
from zf.runtime.result_submit import (
    SemanticResultSubmitService, provision_role_submit_credential, bind_operation_submit_capability,
)
from zf.runtime.workflow_operation import WorkflowOperationService
from zf.runtime.task_contract_snapshot import (
    write_task_contract_snapshot, build_target_snapshot, write_target_snapshot,
)


def _running_verify(tmp_path, profile):
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["result_protocol"]["semantic_submit_profiles"][profile] = "blocking"
    credential_path = provision_role_submit_credential(runtime.state_dir, "verify-1")
    package, spec = _package(runtime.state_dir)
    contract = _contract()
    contract.update(workflow_run_id=RUN_ID, task_map_generation=GENERATION)
    descriptor = write_task_contract_snapshot(runtime.state_dir, contract)
    target = write_target_snapshot(runtime.state_dir, build_target_snapshot(
        descriptor, target_commit=TARGET, contract_snapshot=contract,
    ))
    semantic = _verification_result(contract, descriptor, target)
    semantic.update(workflow_run_id=RUN_ID, task_map_generation=GENERATION,
                    target_commit=TARGET, candidate_ref=CANDIDATE)
    semantic["probe_receipts"][0]["target_commit"] = TARGET
    if profile == "candidate-verify":
        semantic["product_acceptance_report"] = _report(spec)
    payload = {
        **{k: semantic[k] for k in (
            "workflow_run_id", "task_id", "contract_revision", "task_map_generation",
            "base_commit", "task_ref", "contract_snapshot_ref", "contract_snapshot_digest",
            "target_snapshot_ref", "target_snapshot_digest", "target_commit", "candidate_ref",
        )},
        "plan_artifact_package_ref": package["ref"],
        "plan_artifact_package_digest": package["sha256"],
        "role_instance": "verify-1", "fanout_id": "verify", "child_id": "verify-1",
        "stage_id": "verify", "run_id": "attempt-verify",
        "output_profile_id": profile, "output_profile_revision": "1",
        "canonical_success_event": "verify.child.completed",
        "canonical_failure_event": "verify.child.failed",
    }
    operations = WorkflowOperationService(
        state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=runtime.event_writer,
    )
    prepared = SimpleNamespace(operation_id="op-verify")
    ensured = operations.ensure_operation(
        workflow_run_id=RUN_ID, operation_id=prepared.operation_id,
        operation_type="fanout_reader_child", task_id="TASK-1",
        role_instance="verify-1", active_attempt_id="attempt-verify", lease_id="lease-verify",
        request={"output_profile_id": profile, "output_profile_revision": "1",
                 "semantic_result_submit_mode": "blocking", "result_identity": payload,
                 "result_scratch_ref": "tmp/result-submit/op-verify/result.json",
                 "canonical_success_event": "verify.child.completed",
                 "canonical_failure_event": "verify.child.failed"},
    )
    operations.mark_started(
        workflow_run_id=RUN_ID, operation_id=prepared.operation_id,
        request_hash=ensured.request_hash, task_id="TASK-1", dispatch_id="attempt-verify",
        role_instance="verify-1", active_attempt_id="attempt-verify", lease_id="lease-verify",
    )
    bind_operation_submit_capability(runtime.state_dir, operation_id=prepared.operation_id,
        role_instance="verify-1", attempt_id="attempt-verify", lease_id="lease-verify")
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, credential_path, semantic


@pytest.mark.parametrize("profile", ["task-verify", "candidate-verify"])
def test_verifier_preflight_repairs_schema_without_using_capability(tmp_path, profile):
    runtime, prepared, service, credential_path, semantic = _running_verify(tmp_path, profile)
    before = {p.relative_to(runtime.state_dir): p.read_bytes()
              for p in runtime.state_dir.rglob("*") if p.is_file()}
    bad = deepcopy(semantic)
    bad["verdict"] = "blocked"  # All owned requirements passed: cannot claim blocked.
    args = dict(operation_id=prepared.operation_id, role_instance="verify-1",
                credential=credential_path.read_text().strip())
    failed = service.validate_plan_candidate(**args, semantic_result=bad)
    assert failed["status"] == "failed"
    assert any("blocked requirement" in x["message"] for x in failed["errors"])
    passed = service.validate_plan_candidate(**args, semantic_result=semantic)
    assert passed["status"] == "passed", passed
    after = {p.relative_to(runtime.state_dir): p.read_bytes()
             for p in runtime.state_dir.rglob("*") if p.is_file()}
    assert before == after
    submitted = service.submit(**args, semantic_result=semantic)
    assert submitted.canonical_event_type == "verify.child.completed"


@pytest.mark.parametrize("error", ["evidence", "missing_journey", "wrong_target", "missing_report"])
def test_candidate_preflight_uses_full_product_contract(tmp_path, error):
    runtime, prepared, service, credential_path, semantic = _running_verify(tmp_path, "candidate-verify")
    report = semantic["product_acceptance_report"]
    if error == "evidence":
        report["journey_results"][0]["evidence_refs"] = ["event:test"]
    elif error == "missing_journey":
        report["journey_results"] = []
    elif error == "wrong_target":
        report["target_commit"] = "wrong-target"
    else:
        semantic.pop("product_acceptance_report")
    before = [e.id for e in runtime.event_log.read_all()]
    result = service.validate_plan_candidate(
        operation_id=prepared.operation_id, semantic_result=semantic,
        role_instance="verify-1", credential=credential_path.read_text().strip(),
    )
    assert result["status"] == "failed"
    assert any(x["code"] == "product_acceptance_invalid" for x in result["errors"])
    assert before == [e.id for e in runtime.event_log.read_all()]
    assert not (runtime.state_dir / "product-acceptance/reports").exists()


@pytest.mark.parametrize("submit_immediately", [False, True])
def test_verifier_preflight_cli_keeps_binding_for_corrected_submit(tmp_path, monkeypatch, capsys, submit_immediately):
    from zf.cli.main import build_parser

    runtime, prepared, service, credential_path, semantic = _running_verify(tmp_path, "candidate-verify")
    (runtime.project_root / "zf.yaml").write_text(
        'version: "1.0"\nproject: {name: verifier-preflight, state_dir: .zf}\nroles: []\n'
    )
    monkeypatch.chdir(runtime.project_root)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "verify-1")
    monkeypatch.setenv("ZF_RESULT_SUBMIT_TOKEN_FILE", str(credential_path))
    monkeypatch.delenv("ZF_RESULT_SUBMIT_TOKEN", raising=False)
    scratch = runtime.state_dir / "tmp/result-submit/op-verify/result.json"
    scratch.parent.mkdir(parents=True)
    bad = deepcopy(semantic)
    bad["product_acceptance_report"]["journey_results"] = []
    scratch.write_text(json.dumps(bad))
    args = build_parser().parse_args([
        "result", "validate", "--operation", prepared.operation_id,
        "--scratch", "--state-dir", str(runtime.state_dir),
        *(["--submit"] if submit_immediately else []),
    ])
    before = [e.id for e in runtime.event_log.read_all()]
    assert args.func(args) == 1
    captured = capsys.readouterr()
    assert captured.out, captured.err
    assert json.loads(captured.out)["status"] == "failed"
    assert before == [e.id for e in runtime.event_log.read_all()]
    scratch.write_text(json.dumps(semantic))
    assert args.func(args) == 0
    output = json.loads(capsys.readouterr().out)
    if submit_immediately:
        assert output["status"] == "admitted"
        assert output["canonical_event_type"] == "verify.child.completed"
        assert len([e for e in runtime.event_log.read_all() if e.type == "verify.child.completed"]) == 1
        assert args.func(args) == 1  # Consumed capability cannot double-admit.
        return
    assert output["status"] == "passed"
    assert before == [e.id for e in runtime.event_log.read_all()]
    submitted = service.submit(operation_id=prepared.operation_id, use_operation_scratch=True,
        role_instance="verify-1", credential=credential_path.read_text().strip())
    assert submitted.canonical_event_type == "verify.child.completed"
