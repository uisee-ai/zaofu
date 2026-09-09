from zf.core.events.model import ZfEvent
from zf.runtime.workflow_read_result import normalize_workflow_read_result


def test_external_issue_status_is_preserved_as_structured_needs_info() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.completed",
        payload={
            "status": "needs_info",
            "summary": "无法从现有证据推导预期行为",
            "external_issue_triage_status": "needs_info",
        },
    ))
    assert issues == []
    assert result["external_issue_triage_status"] == "needs_info"
    assert result["external_issue_disposition"]["outcome"] == "needs_info"
    assert result["external_issue_disposition"]["questions"]


def test_external_issue_unverified_status_is_preserved() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.completed",
        payload={
            "summary": "范围已确定，动态验证未运行",
            "external_issue_triage_status": "unverified_candidate",
            "verification_status": "partial",
            "verification_gaps": ["runtime stopped"],
        },
    ))
    assert issues == []
    assert result["external_issue_disposition"]["outcome"] == "unverified_candidate"
    assert result["external_issue_triage_status"] == "unverified_candidate"


def test_external_issue_actionable_disposition_uses_summary_when_reason_omitted() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.completed",
        payload={
            "summary": "范围明确，可以进入修复阶段",
            "external_issue_disposition": {
                "outcome": "ready_to_fix",
                "questions": [],
            },
        },
    ))
    assert issues == []
    assert result["external_issue_disposition"]["reason"] == "范围明确，可以进入修复阶段"
