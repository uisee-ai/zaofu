from zf.core.events.model import ZfEvent
from zf.runtime.workflow_read_result import normalize_workflow_read_result
import pytest


@pytest.mark.parametrize("recommendation,expected", [
    ("abstain", "abstained"), ("blocked", "blocked"),
    ("reject", "rejected"), ("needs_rework", "rejected"), ("", "blocked"),
])
def test_reader_unavailable_verdict_survives_repeated_normalization(recommendation, expected):
    report = {"status": "blocked", "recommendation": recommendation,
              "summary": "Reader report", "findings": []}
    if not recommendation:
        report.pop("recommendation")
    for _ in range(3):
        report, issues = normalize_workflow_read_result(ZfEvent(
            type="workflow.child.completed", payload={"report": report}))
        assert issues == []
        assert report["verdict"] == expected


def test_non_artifact_reader_preserves_typed_execution_failure():
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.completed", payload={"report": {
            "status": "blocked", "recommendation": "abstain",
            "failure_class": "reader_execution_failure", "summary": "Probe unavailable",
        }}))
    assert issues == []
    assert result["execution_status"] == "failed"
    assert result["failure_class"] == "reader_execution_failure"
    assert result["verdict"] == "abstained"


def test_reader_rejects_unknown_recommendation_enum():
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.completed", payload={"report": {
            "status": "failed", "recommendation": "rework",
            "summary": "Invalid recommendation", "findings": [],
        }},
    ))

    assert result["recommendation"] == "rework"
    assert any(
        issue["field"] == "control_result.recommendation"
        and issue["code"] == "schema_invalid"
        for issue in issues
    )


@pytest.mark.parametrize("recommendation,expected", [("abstain", "abstained"), ("blocked", "blocked")])
def test_semantic_submit_preserves_unavailable_report(tmp_path, recommendation, expected):
    from tests.test_result_submit import _running_workflow_read_operation
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
    runtime, prepared, service, token = _running_workflow_read_operation(tmp_path)
    submitted = service.submit(operation_id=prepared.operation_id,
        semantic_result={"status": "blocked", "recommendation": recommendation,
                         "summary": "Unavailable; no product rejection", "findings": []},
        role_instance="scan-verification", credential=token)
    event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    result = hydrate_profiled_control_result_event(runtime.state_dir, event).payload["report"]
    assert result["verdict"] == expected
    assert result["failure_class"] != "semantic_rejection"


def test_artifact_production_findings_are_output_not_execution_failure() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.failed",
        payload={
            "result_semantics": "artifact_production",
            "summary": "Scan completed with blocking findings.",
            "findings": [{"severity": "high", "message": "gap"}],
            "recommendation": "needs_rework",
        },
    ))

    assert issues == []
    assert result["execution_status"] == "completed"
    assert result["verdict"] == "passed"
    assert result["subject_verdict"] == "needs_rework"


def test_artifact_production_preserves_explicit_transport_failure() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.failed",
        payload={
            "result_semantics": "artifact_production",
            "summary": "Provider stream stopped.",
            "transport_error": "broken pipe",
        },
    ))

    assert issues == []
    assert result["execution_status"] == "failed"
    assert result["verdict"] == "abstained"
    assert result["failure_class"] == "reader_execution_failure"


def test_structured_artifact_producer_can_report_incomplete_outputs() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="prd.plan.child.failed",
        payload={"report": {
            "result_semantics": "artifact_production",
            "execution_status": "failed",
            "summary": "Draft saved; required task map could not be produced.",
            "artifact_refs": ["artifacts/plan/draft.json"],
            "recommendation": "needs_rework",
        }},
    ))
    assert issues == []
    assert result["execution_status"] == "failed"
    assert result["verdict"] == "abstained"
