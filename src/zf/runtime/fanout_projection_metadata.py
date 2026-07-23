"""Metadata projection helpers for fanout manifests."""

from __future__ import annotations


def apply_report_payload(child: dict, payload: dict) -> None:
    report = payload.get("report")
    if isinstance(report, dict):
        child["report"] = report
        child["report_status"] = str(report.get("status") or "")
        child["recommendation"] = str(report.get("recommendation") or "")
    report_path = _payload_str(payload, "report_path")
    if report_path:
        child["report_path"] = report_path
    diagnostics = payload.get("report_diagnostics")
    if isinstance(diagnostics, list):
        child["report_diagnostics"] = diagnostics


def apply_synth_handoff_metadata(synth: dict, payload: dict) -> None:
    for key in (
        "workflow_run_id",
        "operation_id",
        "request_hash",
        "attempt_id",
        "result_protocol_mode",
        "output_profile_id",
        "output_profile_revision",
        "attempt_source_manifest_ref",
        "attempt_source_manifest_digest",
        "input_consumption_policy_digest",
        "plan_revision",
        "plan_synth_contract_ref",
        "plan_synth_contract_digest",
        "admitted_call_result_digest",
    ):
        value = _payload_str(payload, key)
        if value:
            synth[key] = value
    for key in (
        "attempt_source_manifest",
        "input_consumption_policy_ref",
        "input_consumption_policy",
        "admitted_call_result_ref",
        "control_result_ref",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            synth[key] = dict(value)
    if isinstance(payload.get("required_reads"), list):
        synth["required_reads"] = list(payload["required_reads"])


def _payload_str(payload: object, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    return str(value) if value not in (None, "") else ""


__all__ = ["apply_report_payload", "apply_synth_handoff_metadata"]
