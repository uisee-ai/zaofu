"""Incomplete product reports must not trap truthful execution-failure reporting."""
from copy import deepcopy

import pytest

from tests.test_verification_submit_preflight import _running_verify


@pytest.mark.parametrize("execution,verdict", [("failed", "abstained"), ("completed", "passed")])
def test_preflight_guides_only_execution_abstention_without_weakening_report_checks(
    tmp_path, execution, verdict,
):
    runtime, prepared, service, credential_path, semantic = _running_verify(
        tmp_path, "candidate-verify",
    )
    semantic.update(execution_status=execution, verdict=verdict,
                    failure_class="runner_environment" if execution == "failed" else "none")
    semantic["product_acceptance_report"]["journey_results"][0]["assertion_results"][0]["evidence_refs"] = []
    original = deepcopy(semantic)
    args = dict(operation_id=prepared.operation_id, role_instance="verify-1",
                credential=credential_path.read_text().strip())
    before = {p.relative_to(runtime.state_dir): p.read_bytes()
              for p in runtime.state_dir.rglob("*") if p.is_file()}
    failed = service.validate_plan_candidate(**args, semantic_result=semantic)
    assert failed["status"] == "failed"
    message = next(e["message"] for e in failed["errors"] if e["code"] == "product_acceptance_invalid")
    assert "assertion requires evidence_refs" in message
    assert semantic == original
    assert before == {p.relative_to(runtime.state_dir): p.read_bytes()
                      for p in runtime.state_dir.rglob("*") if p.is_file()}
    if verdict == "passed":
        assert "omit" not in message
        return
    assert "If no product evaluation was possible" in message
    for field in ("product_acceptance_report", "product_acceptance_report_ref", "product_acceptance_report_digest"):
        assert field in message
        semantic.pop(field, None)
    assert service.validate_plan_candidate(**args, semantic_result=semantic)["status"] == "passed"
    submitted = service.submit(**args, semantic_result=semantic)
    assert submitted.canonical_event_type == "verify.child.failed"
    assert not any(e.type == "verify.child.completed" for e in runtime.event_log.read_all())
