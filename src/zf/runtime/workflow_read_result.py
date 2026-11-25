"""Normalize reader call results without coupling them to adapter dispatch."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent


WORKFLOW_READ_RESULT_SCHEMA = "workflow-read-result.v1"
WORKFLOW_READ_PROFILE_ID = "workflow-read"
WORKFLOW_READ_PROFILE_REVISION = "1"

_TOP_LEVEL_HANDOFF_FIELDS = (
    "candidate_kind",
    "owner_decision_items",
    "plan_artifact_sha256",
    "source_index_sha256",
    "artifact_refs",
    "evidence_refs",
    "plan_ports",
    "plan_artifact_ref",
    "plan_ref",
    "task_map_ref",
    "source_index_ref",
    "backlog_ref",
    "scan_quality_audit_ref",
    "output_artifacts",
    "summary",
    "findings",
    "recommendation",
    "verdict",
    "status",
    "execution_status",
    "result_semantics",
    "failure_class",
)


def normalize_workflow_read_result(
    event: ZfEvent,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw = payload.get("report")
    report = dict(raw) if isinstance(raw, Mapping) else {}
    had_structured_report = bool(report)
    handoff_fields = _TOP_LEVEL_HANDOFF_FIELDS
    if (report.get("candidate_kind") or payload.get("candidate_kind")) == "diagnosis_review":
        handoff_fields += ("finding_results", "operator_feedback")
    for field in handoff_fields:
        payload_value = payload.get(field)
        report_value = report.get(field)
        if _empty(report_value) and not _empty(payload_value):
            report[field] = payload_value

    report = project_workflow_read_vote(report)
    subject_verdict = report["subject_verdict"]
    result_semantics = str(
        report.get("result_semantics")
        or payload.get("result_semantics")
        or ""
    ).strip().lower()
    execution_hint = str(report.get("execution_status") or "").lower()
    artifact_output_present = _artifact_production_output_present(report, payload)
    explicit_execution_failure = _explicit_reader_execution_failure(report, payload)

    if result_semantics == "artifact_production" and (
        explicit_execution_failure
        or (had_structured_report and execution_hint == "failed")
        or (
            (event.type.endswith(".failed") or execution_hint == "failed")
            and not artifact_output_present
        )
    ):
        execution_status = "failed"
        verdict = "abstained"
        failure_class = "reader_execution_failure"
    elif result_semantics != "artifact_production" and (
        explicit_execution_failure
        or (event.type.endswith(".failed") and not had_structured_report)
        or execution_hint == "failed"
    ):
        execution_status = "failed"
        verdict = "abstained"
        failure_class = "reader_execution_failure"
    elif result_semantics == "artifact_production":
        execution_status = "completed"
        verdict = "passed"
        failure_class = "none"
    elif subject_verdict in {"rejected", "needs_rework"}:
        execution_status = "completed"
        verdict = "rejected"
        failure_class = "semantic_rejection"
    elif subject_verdict == "blocked":
        execution_status = "completed"
        verdict = "blocked"
        failure_class = "dependency_blocked"
    elif subject_verdict == "abstained":
        execution_status = "completed"
        verdict = "abstained"
        failure_class = "reader_abstained"
    else:
        execution_status = "completed"
        verdict = "passed"
        failure_class = "none"

    report.setdefault("schema_version", WORKFLOW_READ_RESULT_SCHEMA)
    report["execution_status"] = execution_status
    report["verdict"] = verdict
    report["failure_class"] = failure_class
    report["status"] = "passed" if verdict == "passed" else "failed"
    if result_semantics:
        report["result_semantics"] = result_semantics
    report.setdefault(
        "summary",
        str(payload.get("summary") or payload.get("reason") or ""),
    )
    report.setdefault("findings", [])

    from zf.runtime.fanout import REPORT_RECOMMENDATIONS

    issues: list[dict[str, str]] = []
    if not isinstance(report["recommendation"], str) or report["recommendation"] not in REPORT_RECOMMENDATIONS:
        issues.append({
            "field": "control_result.recommendation",
            "code": "schema_invalid",
            "message": f"recommendation must be one of {sorted(REPORT_RECOMMENDATIONS)}; "
                       f"got {report['recommendation']!r}",
        })
    if not str(report.get("summary") or "").strip():
        issues.append({
            "field": "control_result.summary",
            "code": "missing_required",
        })
    issues.extend(workflow_read_finding_issues(report.get("findings")))
    from zf.runtime.task_map_required_ports import plan_input_selection_shape_errors

    gaps = report.get("gap_tasks")
    for index, gap in enumerate(gaps if isinstance(gaps, list) else []):
        evidence = gap.get("evidence_contract") if isinstance(gap, dict) else None
        if not isinstance(evidence, dict) or evidence.get("plan_input_selection") is None:
            continue
        field = f"control_result.gap_tasks[{index}].evidence_contract.plan_input_selection"
        issues.extend({"field": field, "code": "schema_invalid", "message": message}
                      for message in plan_input_selection_shape_errors(
                          evidence["plan_input_selection"], prefix=field))
    return report, issues


def workflow_read_finding_issues(
    findings: Any, *, field: str = "control_result.findings",
) -> list[dict[str, str]]:
    """Check the exact downstream finding contract before consuming a result."""
    from zf.runtime.fanout import validate_fanout_report

    validation = validate_fanout_report({"findings": findings}, child_id="reader")
    return [
        {"field": field, "code": "schema_invalid", "message": message}
        for message in validation.diagnostics
    ]


def project_workflow_read_vote(report: Mapping[str, Any]) -> dict[str, Any]:
    """Translate Reader outcomes to a fanout vote without changing a stored result."""
    vote = report.get("recommendation")
    recommendation = str(vote or report.get("verdict") or "").lower()
    status = str(report.get("status") or "").lower()
    subject = _subject_verdict(recommendation=recommendation, status=status)
    # The vote alone cannot distinguish a dependency blocker from a voluntary abstention.
    if recommendation in {"", "abstain", "abstained"} and (
        report.get("failure_class") == "dependency_blocked"
        or report.get("verdict") == "blocked"
    ):
        subject = "blocked"
    canonical_vote = {
        "passed": "approve", "rejected": "reject", "needs_rework": "needs_rework",
        "abstained": "abstain", "blocked": "abstain",
    }[subject]
    normalized = {**report, "subject_verdict": subject}
    normalized.setdefault("recommendation", canonical_vote)
    return project_admitted_workflow_read_vote(normalized)


def project_admitted_workflow_read_vote(report: Mapping[str, Any]) -> dict[str, Any]:
    """Map historical vote spellings, preserving every admitted semantic field."""
    projected = dict(report)
    vote = projected.get("recommendation")
    if isinstance(vote, str):
        projected["recommendation"] = {
            "rejected": "reject", "abstained": "abstain", "blocked": "abstain", "block": "abstain",
        }.get(vote, vote)
    return projected


def is_workflow_read_report_event(event: ZfEvent) -> bool:
    """Scope Reader vote compatibility to Reader results, including inline legacy events."""
    payload = event.payload
    if any(isinstance(payload.get(key), dict) for key in (
        "verification_result", "goal_closure_result", "artifact_delivery_result",
    )):
        return False
    report = payload.get("report")
    if not isinstance(report, dict):
        return False
    profile = payload.get("semantic_result_profile")
    profile_id = profile.get("profile_id") if isinstance(profile, dict) else None
    profile_id = profile_id or payload.get("output_profile_id")
    if profile_id:
        return profile_id == WORKFLOW_READ_PROFILE_ID
    return (report.get("schema_version") == WORKFLOW_READ_RESULT_SCHEMA
            or event.type in {"workflow.child.completed", "workflow.child.failed"})


def _subject_verdict(*, recommendation: str, status: str) -> str:
    if recommendation == "needs_rework":
        return "needs_rework"
    if recommendation in {"reject", "rejected"}:
        return "rejected"
    if recommendation in {"block", "blocked"}:
        return "blocked"
    if recommendation in {"abstain", "abstained"}:
        return "abstained"
    # Canonical status=failed includes unavailable outcomes; do not reinterpret
    # an explicit subject decision on the second admission/adapter pass.
    if status in {"failed", "rejected"}:
        return "rejected"
    if status == "blocked":
        return "blocked"
    return "passed"


def _artifact_production_output_present(
    report: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    for source in (report, payload):
        if str(source.get("summary") or "").strip():
            return True
        for field in (
            "findings",
            "artifact_refs",
            "evidence_refs",
            "output_artifacts",
            "plan_ports",
        ):
            value = source.get(field)
            if isinstance(value, list) and value:
                return True
        for field in (
            "plan_artifact_ref",
            "plan_ref",
            "task_map_ref",
            "source_index_ref",
            "backlog_ref",
            "scan_quality_audit_ref",
        ):
            if str(source.get(field) or "").strip():
                return True
    return False


def _explicit_reader_execution_failure(
    report: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    failure_class = str(
        report.get("failure_class")
        or payload.get("failure_class")
        or ""
    ).strip().lower()
    if failure_class in {
        "provider_execution_failure",
        "reader_execution_failure",
        "protocol_failure",
        "transport_failure",
    }:
        return True
    return any(
        bool(source.get(field))
        for source in (report, payload)
        for field in ("provider_error", "protocol_error", "transport_error")
    )


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


__all__ = [
    "WORKFLOW_READ_PROFILE_ID",
    "WORKFLOW_READ_PROFILE_REVISION",
    "WORKFLOW_READ_RESULT_SCHEMA",
    "normalize_workflow_read_result",
]
