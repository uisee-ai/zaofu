"""Plan execution failure must settle without inventing a successful Plan."""
from pathlib import Path

import pytest

from tests.test_result_submit import _running_plan_candidate_operation
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event


def _failure():
    return {
        "execution_status": "failed", "status": "failed",
        "recommendation": "needs_rework", "summary": "Required independent scan is missing.",
        "findings": [], "plan_content_profile": "summary_only", "plan_ports": [],
    }


@pytest.mark.parametrize("artifact_production", [False, True])
def test_failed_planner_validates_and_submits_without_success_ports(tmp_path: Path, artifact_production: bool):
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    report = _failure()
    if artifact_production:
        report.update(result_semantics="artifact_production", failure_class="reader_execution_failure")
    args = dict(operation_id=prepared.operation_id, semantic_result=report,
                role_instance="planner", credential=token)
    before = len(runtime.event_log.read_all())
    result = service.validate_plan_candidate(**args, project_root=runtime.project_root)
    assert result["status"] == "passed"
    assert len(runtime.event_log.read_all()) == before
    submitted = service.submit(**args)
    assert submitted.canonical_event_type == "issue.triage.child.failed"
    event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, event)
    assert hydrated.payload["report"]["execution_status"] == "failed"
    assert hydrated.payload["report"]["verdict"] == "abstained"
    settled = [e for e in runtime.event_log.read_all() if e.type == "workflow.operation.settled"]
    assert len(settled) == 1
    assert settled[0].payload["operation_id"] == prepared.operation_id


@pytest.mark.parametrize("mutation", ["missing_summary", "bad_findings", "completed", "semantic_rejection", "artifact_without_explicit_failure"])
def test_failure_path_does_not_bypass_result_or_success_validation(tmp_path: Path, mutation: str):
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    report = _failure()
    if mutation == "missing_summary":
        report.pop("summary")
    elif mutation == "bad_findings":
        report["findings"] = "invalid"
    elif mutation == "completed":
        report["execution_status"] = "completed"
    elif mutation == "semantic_rejection":
        report.pop("execution_status")
    else:
        report["result_semantics"] = "artifact_production"
    args = dict(operation_id=prepared.operation_id, semantic_result=report,
                role_instance="planner", credential=token)
    assert service.validate_plan_candidate(**args)["status"] == "failed"
    with pytest.raises(ResultSubmitError):
        service.submit(**args)
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())
