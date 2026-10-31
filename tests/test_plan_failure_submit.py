from pathlib import Path

import pytest

from tests.test_result_submit import _running_plan_candidate_operation
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.result_submit import ResultSubmitError


def _failure() -> dict:
    return {
        "execution_status": "failed",
        "status": "failed",
        "recommendation": "needs_rework",
        "summary": "Planning output is incomplete; no dispatchable task map exists.",
        "findings": [{"severity": "high", "message": "Complete task ownership and DAG."}],
    }


@pytest.mark.parametrize("result_semantics", ["", "artifact_production"])
def test_plan_execution_failure_can_validate_and_submit_without_success_outputs(
    tmp_path: Path, result_semantics: str,
) -> None:
    runtime, prepared, service, token = _running_plan_candidate_operation(
        tmp_path, result_semantics=result_semantics,
    )
    before = [e.id for e in runtime.event_log.read_all()]
    result = service.validate_plan_candidate(
        operation_id=prepared.operation_id, semantic_result=_failure(),
        role_instance="planner", credential=token, project_root=runtime.project_root,
    )
    assert result["status"] == "passed"
    assert result["execution_status"] == "failed"
    assert [e.id for e in runtime.event_log.read_all()] == before
    submitted = service.submit(
        operation_id=prepared.operation_id, semantic_result=_failure(),
        role_instance="planner", credential=token,
    )
    assert submitted.canonical_event_type == "issue.triage.child.failed"
    events = runtime.event_log.read_all()
    canonical = next(e for e in events if e.type == "issue.triage.child.failed")
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, canonical)
    assert hydrated.payload["report"]["execution_status"] == "failed"
    assert hydrated.payload["report"]["verdict"] != "passed"
    assert not any(e.type == "issue.triage.child.completed" for e in events)
    assert any(e.type == "workflow.call.result.admitted" for e in events)


def test_subject_rejection_still_requires_a_complete_plan_candidate(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    semantic = _failure()
    semantic.pop("execution_status")
    with pytest.raises(ResultSubmitError, match="Plan candidate failed"):
        service.submit(
            operation_id=prepared.operation_id, semantic_result=semantic,
            role_instance="planner", credential=token,
        )
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())


def test_plan_failure_still_requires_typed_report_and_authorization(tmp_path: Path) -> None:
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    with pytest.raises(ResultSubmitError):
        service.submit(
            operation_id=prepared.operation_id, semantic_result=_failure(),
            role_instance="planner", credential="invalid-credential",
        )
    semantic = {**_failure(), "summary": "", "findings": "invalid"}
    result = service.validate_plan_candidate(
        operation_id=prepared.operation_id, semantic_result=semantic,
        role_instance="planner", credential=token, project_root=runtime.project_root,
    )
    assert result["status"] == "failed"
    assert {e["code"] for e in result["errors"]} == {"missing_required", "schema_invalid"}
    with pytest.raises(ResultSubmitError, match="Plan candidate failed"):
        service.submit(
            operation_id=prepared.operation_id, semantic_result=semantic,
            role_instance="planner", credential=token,
        )
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())
