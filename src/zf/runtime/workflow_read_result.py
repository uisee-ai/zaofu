"""Normalize reader call results without coupling them to adapter dispatch."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent


WORKFLOW_READ_RESULT_SCHEMA = "workflow-read-result.v1"
WORKFLOW_READ_PROFILE_ID = "workflow-read"
WORKFLOW_READ_PROFILE_REVISION = "1"

_TOP_LEVEL_HANDOFF_FIELDS = (
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
    for field in _TOP_LEVEL_HANDOFF_FIELDS:
        payload_value = payload.get(field)
        report_value = report.get(field)
        if _empty(report_value) and not _empty(payload_value):
            report[field] = payload_value

    status = str(report.get("status") or payload.get("status") or "").lower()
    recommendation = str(
        report.get("recommendation")
        or report.get("verdict")
        or payload.get("recommendation")
        or ""
    ).lower()
    result_semantics = str(
        report.get("result_semantics")
        or payload.get("result_semantics")
        or ""
    ).strip().lower()
    execution_hint = str(report.get("execution_status") or "").lower()
    subject_verdict = _subject_verdict(
        recommendation=recommendation,
        status=status,
    )
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
        (event.type.endswith(".failed") and not had_structured_report)
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
    report["subject_verdict"] = subject_verdict
    if result_semantics:
        report["result_semantics"] = result_semantics
    report.setdefault("recommendation", "approve" if verdict == "passed" else "reject")
    report.setdefault(
        "summary",
        str(payload.get("summary") or payload.get("reason") or ""),
    )
    report.setdefault("findings", [])

    issues: list[dict[str, str]] = []
    if not str(report.get("summary") or "").strip():
        issues.append({
            "field": "control_result.summary",
            "code": "missing_required",
        })
    issues.extend(workflow_read_finding_issues(report.get("findings")))
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


def _subject_verdict(*, recommendation: str, status: str) -> str:
    if recommendation == "needs_rework":
        return "needs_rework"
    if recommendation in {"reject", "rejected"} or status in {"failed", "rejected"}:
        return "rejected"
    if recommendation in {"block", "blocked"} or status == "blocked":
        return "blocked"
    if recommendation in {"abstain", "abstained"}:
        return "abstained"
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
