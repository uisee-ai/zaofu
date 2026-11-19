"""Task audits cannot be mistaken for the final full-product evaluation."""
from copy import deepcopy

import pytest

from tests.test_verification_submit_preflight import _running_verify


def test_audit_passes_task_preflight_and_admission_without_global_product_report(tmp_path):
    runtime, prepared, service, credential, semantic = _running_verify(
        tmp_path, "task-verify", owner="candidate_verify", pipeline=True,
    )
    args = dict(operation_id=prepared.operation_id, role_instance="verify-1",
                credential=credential.read_text().strip())
    before = {p.relative_to(runtime.state_dir): p.read_bytes()
              for p in runtime.state_dir.rglob("*") if p.is_file()}
    checked = service.validate_plan_candidate(**args, semantic_result=semantic)
    assert checked["status"] == "passed", checked
    assert before == {p.relative_to(runtime.state_dir): p.read_bytes()
                      for p in runtime.state_dir.rglob("*") if p.is_file()}
    submitted = service.submit(**args, semantic_result=semantic)
    assert submitted.canonical_event_type == "verify.child.completed"
    assert not any(e.type in {"run.goal.completed", "judge.passed"}
                   for e in runtime.event_log.read_all())
    assert not (runtime.state_dir / "artifacts/product-acceptance/reports").exists()


@pytest.mark.parametrize("bad", ["missing_criterion", "wrong_target", "missing_probe"])
def test_audit_keeps_its_own_acceptance_and_target_gates(tmp_path, bad):
    runtime, prepared, service, credential, semantic = _running_verify(
        tmp_path, "task-verify", owner="candidate_verify", pipeline=True,
    )
    if bad == "missing_criterion":
        semantic["requirement_results"] = []
    elif bad == "wrong_target":
        semantic["probe_receipts"][0]["target_commit"] = "wrong"
    else:
        semantic["probe_receipts"] = []
    checked = service.validate_plan_candidate(operation_id=prepared.operation_id,
        role_instance="verify-1", credential=credential.read_text().strip(), semantic_result=semantic)
    assert checked["status"] == "failed"
    assert any(x["code"] != "product_acceptance_invalid" for x in checked["errors"]), checked


@pytest.mark.parametrize("profile,pipeline", [("candidate-verify", True), ("candidate-verify", False), ("task-verify", False)])
def test_task_claim_cannot_remove_candidate_or_legacy_global_acceptance(tmp_path, profile, pipeline):
    _, prepared, service, credential, semantic = _running_verify(
        tmp_path, profile, owner="candidate_verify", pipeline=pipeline,
    )
    semantic = deepcopy(semantic)
    semantic.pop("product_acceptance_report", None)
    semantic.update(operation_type="task-stage", task_pipeline_stage="verify", output_profile_id="task-verify")
    checked = service.validate_plan_candidate(operation_id=prepared.operation_id,
        role_instance="verify-1", credential=credential.read_text().strip(), semantic_result=semantic)
    assert checked["status"] == "failed"
    assert any(x["code"] == "product_acceptance_invalid" for x in checked["errors"]), checked
