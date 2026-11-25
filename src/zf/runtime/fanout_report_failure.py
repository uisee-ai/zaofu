"""Normalize fanout findings and project report failures into replan evidence."""

from __future__ import annotations

from typing import Any


_REPORT_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_REPORT_SEVERITY_ALIASES = {
    "blocker": "high",
    "blocking": "high",
    "error": "high",
    "major": "high",
    "minor": "low",
    "warn": "medium",
    "warning": "medium",
}
_FINDING_DETAIL_KEYS = (
    "finding_id",
    "code",
    "field",
    "task_id",
    "acceptance_id",
    "evidence_refs",
    "observed",
    "expected",
    "observed_gap",
    "required_change",
    "done_when",
    "owner",
    "next_gate",
    "allowed_scope",
)


def normalize_finding(
    raw: dict[str, Any],
    index: int,
    diagnostics: list[str],
    *,
    source_name: str = "findings",
) -> dict[str, Any]:
    severity = str(raw.get("severity") or "info").strip().lower()
    severity = _REPORT_SEVERITY_ALIASES.get(severity, severity)
    if severity not in _REPORT_SEVERITIES:
        diagnostics.append(
            f"{source_name}[{index}].severity must be one of "
            f"{sorted(_REPORT_SEVERITIES)}; got {severity!r}"
        )
        severity = "info"

    category = raw.get(
        "category",
        raw.get("type", raw.get("code", raw.get("id", ""))),
    )
    if not isinstance(category, str):
        diagnostics.append(f"{source_name}[{index}].category must be a string")
        category = str(category)

    path = raw.get("path", raw.get("file", ""))
    if not isinstance(path, str):
        diagnostics.append(f"{source_name}[{index}].path must be a string")
        path = str(path)
    line = raw.get("line")
    if line in (None, "") and ":" in path:
        maybe_path, maybe_line = path.rsplit(":", 1)
        if maybe_line.isdigit():
            path = maybe_path
            line = maybe_line

    message = raw.get(
        "message",
        raw.get("summary", raw.get("description", raw.get("reason", ""))),
    )
    if not message:
        parts = [
            (label, raw.get(key))
            for label, key in (
                ("Observed", "observed"),
                ("Expected", "expected"),
                ("Observed gap", "observed_gap"),
                ("Required change", "required_change"),
                ("Done when", "done_when"),
            )
            if str(raw.get(key) or "").strip()
        ]
        message = "; ".join(f"{label}: {value}" for label, value in parts)
    if not isinstance(message, str):
        diagnostics.append(
            f"{source_name}[{index}].message must be a non-empty string"
        )
        message = str(message)
    message = message.strip()
    if not message:
        diagnostics.append(
            f"{source_name}[{index}].message must be a non-empty string"
        )

    finding: dict[str, Any] = {
        "severity": severity,
        "category": category,
        "path": path,
        "message": message,
    }
    if line not in (None, ""):
        try:
            line_int = int(line)
        except (TypeError, ValueError):
            diagnostics.append(
                f"{source_name}[{index}].line must be a positive integer"
            )
        else:
            if line_int < 1:
                diagnostics.append(
                    f"{source_name}[{index}].line must be a positive integer"
                )
            else:
                finding["line"] = line_int
    for key in _FINDING_DETAIL_KEYS:
        if raw.get(key) not in (None, "", [], {}):
            finding[key] = raw[key]
    return finding


def project_report_failure(
    artifact_payload: dict[str, Any],
    report: dict[str, Any],
    diagnostics: list[str],
) -> list[dict[str, Any]]:
    failure_class = str(report.get("failure_class") or "").strip()
    if failure_class:
        artifact_payload["failure_class"] = failure_class
    raw_findings = report.get("findings")
    findings = list(raw_findings) if isinstance(raw_findings, list) else []
    normalized_diagnostics = list(diagnostics)
    raw_fix_items = report.get("fix_items")
    if raw_fix_items not in (None, "") and not isinstance(raw_fix_items, list):
        normalized_diagnostics.append("fix_items must be a list")
        raw_fix_items = []
    if isinstance(raw_fix_items, list):
        artifact_payload["fix_items"] = list(raw_fix_items)
        for index, raw_fix_item in enumerate(raw_fix_items):
            if not isinstance(raw_fix_item, dict):
                normalized_diagnostics.append(
                    f"fix_items[{index}] must be an object"
                )
                continue
            fix_item = dict(raw_fix_item)
            fix_item.setdefault("severity", "high")
            fix_item.setdefault(
                "category",
                str(
                    fix_item.get("code")
                    or fix_item.get("acceptance_id")
                    or "fix-item"
                ),
            )
            normalized = normalize_finding(
                fix_item,
                index,
                normalized_diagnostics,
                source_name="fix_items",
            )
            normalized["source"] = "fix_item"
            duplicate = any(
                isinstance(item, dict)
                and item.get("category") == normalized.get("category")
                and item.get("path") == normalized.get("path")
                and item.get("message") == normalized.get("message")
                for item in findings
            )
            if not duplicate:
                findings.append(normalized)

    if not normalized_diagnostics:
        return findings
    artifact_payload["report_diagnostics"] = normalized_diagnostics
    schema_message = "; ".join(normalized_diagnostics)
    if not any(
        isinstance(item, dict)
        and item.get("category") == "report-schema"
        and item.get("message") == schema_message
        for item in findings
    ):
        findings.append({
            "severity": "high",
            "category": "report-schema",
            "path": str(report.get("plan_artifact_ref") or ""),
            "message": schema_message,
        })
    return findings


def project_synth_failure_payload(
    artifact_payload: dict[str, Any],
    payload: dict[str, Any],
    report: dict[str, Any],
    diagnostics: list[str],
    *, verified_diagnosis_handoff: bool = False,
) -> dict[str, Any]:
    summary = str(
        report.get("summary") or payload.get("summary") or payload.get("reason") or ""
    )
    findings = project_report_failure(artifact_payload, report, diagnostics)
    if summary:
        artifact_payload["summary"] = summary
    if findings:
        artifact_payload["findings"] = findings
    for key in (
        "failure_class",
        "failure_kind",
        "semantic_attempt_consumed",
        "plan_review_optimization",
        "evidence_refs",
        "attempt_domain",
        "semantic_rework_cost",
        "plan_candidate_preflight_ref",
        "previous_plan_candidate_refs",
        "owner_confirmation_ref",
        "owner_confirmation",
        "owner_decision_items",
        "owner_decision_resolution",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            artifact_payload[key] = value
    if verified_diagnosis_handoff:
        for key in ("failure_scope", "planner_admitted_event_id", "planner_result_ref", "planner_request_hash"):
            if key in payload:
                artifact_payload[key] = payload[key]
    return artifact_payload


__all__ = [
    "normalize_finding",
    "project_report_failure",
    "project_synth_failure_payload",
]
