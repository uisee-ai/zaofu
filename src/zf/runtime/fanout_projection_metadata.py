"""Metadata projection helpers for fanout manifests."""

from __future__ import annotations

from zf.runtime.task_contract_snapshot import TASK_CONTRACT_AUTHORITY_FIELDS


def apply_child_metadata(child: dict, payload: dict) -> None:
    for key in (
        *TASK_CONTRACT_AUTHORITY_FIELDS,
        "task_id", "scope", "workdir", "source_branch", "source_commit",
        "task_ref", "pdd_id", "feature_id", "task_map_ref", "source_index_ref",
        "retry_of_run_id", "expected_output", "owner_claim", "risk",
        "assignment_strategy", "lane_profile", "lane_id", "stage_slot",
        "affinity_tag", "pipeline_id", "root_fanout_id",
        "upstream_root_fanout_id", "upstream_fanout_id", "upstream_child_id",
        "upstream_task_id", "upstream_stage_slot", "queue_order", "briefing_path",
        "contract_revision", "task_map_generation", "base_commit",
        "contract_snapshot_ref", "contract_snapshot_digest", "target_snapshot_ref",
        "target_commit", "target_snapshot_digest",
        "impl_self_check_ref", "impl_self_check_digest",
        "operation_id", "parent_operation_id", "request_hash", "attempt_id",
        "result_protocol_mode", "attempt_source_manifest_ref",
        "attempt_source_manifest_digest", "input_consumption_policy_digest",
        "admitted_call_result_digest", "semantic_verdict",
    ):
        value = _payload_str(payload, key)
        if value:
            child[key] = value
    if "attempt" in payload:
        child["attempt"] = payload.get("attempt")
    skills = payload.get("skills")
    if isinstance(skills, list):
        child["skills"] = [str(skill) for skill in skills]
    for key in ("artifact_refs", "evidence_refs"):
        values = payload.get(key)
        if isinstance(values, list):
            child[key] = list(values)
    for key in (
        "attempt_source_manifest", "input_consumption_policy_ref",
        "input_consumption_policy", "admitted_call_result_ref", "control_result_ref",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            child[key] = dict(value)
    if isinstance(payload.get("required_reads"), list):
        child["required_reads"] = list(payload["required_reads"])
    source_refs = payload.get("source_refs")
    if isinstance(source_refs, dict):
        child["source_refs"] = dict(source_refs)
    for key in (
        "workflow_run_id",
        "workflow_input_manifest_ref",
        "workflow_prompt_ref",
        "prompt_kind",
        "channel_id",
        "thread_id",
        "pattern_id",
        "request_id",
    ):
        value = _payload_str(payload, key)
        if value:
            child[key] = value
    if "request_revision" in payload:
        try:
            child["request_revision"] = int(
                payload.get("request_revision") or 0
            )
        except (TypeError, ValueError):
            child["request_revision"] = 0
    if isinstance(payload.get("origin_binding"), dict):
        child["origin_binding"] = dict(payload["origin_binding"])
    verification_result = payload.get("verification_result")
    if isinstance(verification_result, dict):
        child["verification_result"] = dict(verification_result)


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
        "reviewed_plan_candidate_digest",
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


__all__ = ["apply_child_metadata", "apply_report_payload", "apply_synth_handoff_metadata"]
