"""Deterministic identity and failure facts for candidate integration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def stable_payload_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def candidate_integration_identity(
    *,
    run_id: str,
    task_map_generation: str,
    fanout_id: str,
    candidate_payload: Mapping[str, Any],
) -> dict[str, str]:
    environment = _mapping(candidate_payload.get("candidate_environment"))
    quality = _mapping(candidate_payload.get("quality"))
    environment_digest = str(environment.get("receipt_digest") or "")
    if not environment_digest:
        environment_digest = stable_payload_digest(
            _environment_identity_payload(environment),
        )
    quality_digest = stable_payload_digest(_quality_identity_payload(quality))
    candidate_input_digest = stable_payload_digest(
        _candidate_input_binding(candidate_payload),
    )
    identity = {
        "workflow_run_id": run_id,
        "task_map_generation": task_map_generation,
        "fanout_id": fanout_id,
        "candidate_input_digest": candidate_input_digest,
        "candidate_environment_digest": environment_digest,
        "candidate_quality_digest": quality_digest,
    }
    return {
        "integration_attempt_id": "candidate-integration-" + stable_payload_digest(identity)[:24],
        "candidate_input_digest": candidate_input_digest,
        "candidate_environment_digest": environment_digest,
        "candidate_quality_digest": quality_digest,
    }


def candidate_failure_envelope(
    candidate_payload: Mapping[str, Any],
    *,
    failed_children: list[str],
) -> dict[str, Any]:
    status = str(candidate_payload.get("status") or "")
    quality = _mapping(candidate_payload.get("quality"))
    environment = _mapping(candidate_payload.get("candidate_environment"))
    evidence = _first_failed_check(quality)
    command = str(evidence.get("command") or "")
    exit_code = evidence.get("exit_code")
    diagnostics = _diagnostic_summary(evidence, quality, failed_children)

    if status == "conflict" and _is_candidate_merge_conflict(candidate_payload):
        failure_class = "candidate_integration_conflict"
        reason = str(candidate_payload.get("error") or "candidate integration conflict")
    elif (
        str(environment.get("status") or "") == "failed"
        or str(quality.get("failure") or "") == "candidate_environment_setup_failed"
    ):
        failure_class = "candidate_environment_setup_failed"
        reason = str(environment.get("detail") or "candidate environment setup failed")
    elif _looks_like_gate_contract_mismatch(diagnostics):
        failure_class = "candidate_quality_gate_contract_mismatch"
        reason = (
            "candidate quality gate is not declared by the integrated project: "
            f"{command or _failed_gate_names(quality)}"
        )
    elif _looks_like_missing_dependency(diagnostics):
        failure_class = "candidate_dependency_missing"
        reason = f"candidate dependency unavailable while running {command or 'quality gate'}"
    elif status == "stale":
        failure_class = "candidate_contract_failure"
        reason = "candidate inputs are stale or violate the task contract"
    elif status == "quality_failed":
        failure_class = "candidate_product_quality_failed"
        reason = f"candidate quality gate failed: {command or _failed_gate_names(quality)}"
    elif failed_children:
        failure_class = "candidate_integration_failure"
        reason = "candidate integration could not consume all writer results"
    else:
        failure_class = "candidate_integration_failure"
        reason = "candidate integration failed"

    fingerprint_body = {
        "failure_class": failure_class,
        "primary_failure_reason": reason,
        "failing_command": command,
        "exit_code": exit_code,
        "diagnostic_class": _diagnostic_class(diagnostics),
        "candidate_environment_digest": stable_payload_digest(
            _environment_identity_payload(environment),
        ),
        "candidate_quality_digest": stable_payload_digest(
            _quality_identity_payload(quality),
        ),
        "candidate_input_digest": stable_payload_digest(
            _candidate_input_binding(candidate_payload),
        ),
        "failed_children": sorted(failed_children),
    }
    return {
        "failure_class": failure_class,
        "failure_scope": "candidate",
        "primary_failure_reason": reason,
        "diagnostic_summary": diagnostics,
        "failing_command": command,
        "exit_code": exit_code,
        "setup_script_digest": str(environment.get("setup_script_digest") or ""),
        "failure_fingerprint": "candidate-failure-" + stable_payload_digest(
            fingerprint_body,
        )[:24],
    }


def prepare_candidate_integration_attempt(
    *,
    candidate_contract_payload: dict[str, Any],
    candidate_payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    events: list[ZfEvent],
    event_writer: EventWriter,
    final_status: str,
    publish_event: str,
    failed_children: list[str],
    fanout_id: str,
    trace_id: str,
) -> bool:
    """Bind stable attempt facts and suppress an already-settled replay."""

    run_id = str(
        candidate_contract_payload.get("workflow_run_id")
        or candidate_contract_payload.get("run_id")
        or trace_id
    )
    trigger_payload = _mapping(manifest.get("trigger_payload"))
    generation = str(
        candidate_contract_payload.get("task_map_generation")
        or trigger_payload.get("task_map_generation")
        or manifest.get("task_map_generation")
        or ""
    )
    if generation:
        candidate_contract_payload["task_map_generation"] = generation
    identity = candidate_integration_identity(
        run_id=run_id,
        task_map_generation=generation,
        fanout_id=fanout_id,
        candidate_payload=candidate_payload,
    )
    candidate_contract_payload.update(identity)
    if final_status == "failed":
        candidate_contract_payload.update(candidate_failure_envelope(
            candidate_payload,
            failed_children=failed_children,
        ))
    terminal = next((
        event
        for event in reversed(events)
        if event.type == publish_event
        and str(_mapping(event.payload).get("integration_attempt_id") or "")
        == identity["integration_attempt_id"]
    ), None)
    if terminal is None:
        return False
    duplicate_recorded = any(
        event.type == "candidate.integration.duplicate_suppressed"
        and str(_mapping(event.payload).get("integration_attempt_id") or "")
        == identity["integration_attempt_id"]
        for event in events
    )
    if not duplicate_recorded:
        event_writer.append(ZfEvent(
            type="candidate.integration.duplicate_suppressed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                **identity,
                "terminal_event_id": terminal.id,
                "terminal_event_type": terminal.type,
                "reason": "identical candidate integration attempt already settled",
            },
            causation_id=terminal.id,
            correlation_id=trace_id,
        ))
    return True


def candidate_terminal_event_payload(
    *,
    contract_payload: Mapping[str, Any],
    candidate_payload: Mapping[str, Any],
    fanout_id: str,
    trace_id: str,
    stage_id: str,
    status: str,
    failed_children: list[str],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "fanout_id": fanout_id,
        "trace_id": trace_id,
        "stage_id": stage_id,
        "status": status,
        **contract_payload,
        "commit": str(candidate_payload.get("commit") or ""),
        "failed_children": failed_children,
        "findings": findings,
        "product_findings": (
            findings
            if contract_payload.get("failure_class")
            == "candidate_product_quality_failed"
            else []
        ),
    }


def candidate_aggregate_event_payload(
    *,
    contract_payload: Mapping[str, Any],
    candidate_payload: Mapping[str, Any],
    fanout_id: str,
    trace_id: str,
    stage_id: str,
    status: str,
    success_event: str,
    failure_event: str,
    pdd_id: str,
    feature_id: str,
    task_map_ref: str,
    source_index_ref: str,
    completed_task_ids: list[str],
    failed_children: list[str],
    findings: list[dict[str, Any]],
    recovered_status: str,
    recovered_reason: str,
) -> dict[str, Any]:
    return {
        "fanout_id": fanout_id,
        "trace_id": trace_id,
        "stage_id": stage_id,
        "status": status,
        "success_event": success_event if status == "completed" else "",
        "failure_event": failure_event if status == "failed" else "",
        "pdd_id": pdd_id,
        "feature_id": feature_id,
        "task_map_ref": task_map_ref,
        "source_index_ref": source_index_ref,
        "completed_task_ids": completed_task_ids,
        "failed_children": failed_children,
        "candidate_status": str(candidate_payload.get("status") or ""),
        "candidate_ref": str(candidate_payload.get("branch") or ""),
        "findings": findings if status == "failed" else [],
        "recovered_from_aggregate_status": recovered_status,
        "recovered_from_aggregate_reason": recovered_reason,
        **contract_payload,
    }
def _first_failed_check(quality: Mapping[str, Any]) -> dict[str, Any]:
    gate_checks = quality.get("gate_checks")
    if not isinstance(gate_checks, Mapping):
        return {}
    for checks in gate_checks.values():
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, Mapping):
                continue
            if (
                check.get("exit_code") not in (0, "0")
                or check.get("timed_out")
                or check.get("error")
            ):
                return dict(check)
    return {}


def _diagnostic_summary(
    evidence: Mapping[str, Any],
    quality: Mapping[str, Any],
    failed_children: list[str],
) -> str:
    values = [
        evidence.get("error"),
        evidence.get("stderr_tail"),
        evidence.get("stdout_tail"),
    ]
    for value in values:
        text = str(value or "").strip()
        if text:
            return text[:1000]
    details = quality.get("failure_details")
    if isinstance(details, Mapping):
        flattened = [
            str(item).strip()
            for items in details.values()
            for item in (items if isinstance(items, list) else [items])
            if str(item).strip()
        ]
        if flattened:
            return "; ".join(flattened)[:1000]
    return "; ".join(failed_children)[:1000]


def _looks_like_missing_dependency(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in (
        "command not found",
        "not found",
        "no such file or directory",
        "cannot find module",
        "module not found",
    ))


def _looks_like_gate_contract_mismatch(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in (
        "missing script:",
        "missing script ",
        "unknown script",
        "script not found",
        "couldn't find a script named",
        "command \"run\" not found",
    ))


def _diagnostic_class(text: str) -> str:
    if _looks_like_gate_contract_mismatch(text):
        return "quality_gate_contract_mismatch"
    if _looks_like_missing_dependency(text):
        return "dependency_missing"
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    return "command_failed" if text.strip() else "unknown"


def _is_candidate_merge_conflict(candidate_payload: Mapping[str, Any]) -> bool:
    conflict_files = candidate_payload.get("conflict_files")
    if isinstance(conflict_files, list) and any(
        str(item or "").strip() for item in conflict_files
    ):
        return True
    error = str(candidate_payload.get("error") or "").lower()
    return any(marker in error for marker in (
        "merge conflict",
        "cherry-pick conflict",
        "cherry pick conflict",
        "conflict markers",
        "overlapping git-derived changed_files",
    ))


def _failed_gate_names(quality: Mapping[str, Any]) -> str:
    names = quality.get("gates_failed")
    if isinstance(names, list):
        return ", ".join(str(name) for name in names if str(name)) or "unknown gate"
    return "unknown gate"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _candidate_input_binding(candidate_payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate_inputs = candidate_payload.get("included_tasks")
    if not isinstance(candidate_inputs, list) or not candidate_inputs:
        candidate_inputs = candidate_payload.get("requested_tasks")
    if isinstance(candidate_inputs, list) and candidate_inputs:
        return {
            "base_commit": str(candidate_payload.get("base_commit") or ""),
            "tasks": sorted(
                (
                    str(item.get("task_id") or ""),
                    str(item.get("task_ref") or ""),
                    str(item.get("source_commit") or ""),
                )
                for item in candidate_inputs
                if isinstance(item, Mapping)
            ),
        }
    return {"candidate_head_commit": str(candidate_payload.get("commit") or "")}


def _environment_identity_payload(environment: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: environment.get(key)
        for key in (
            "schema_version",
            "status",
            "setup_declared",
            "setup_script_digest",
            "setup_ran",
            "exit_code",
        )
        if environment.get(key) not in (None, "")
    }


def _quality_identity_payload(quality: Mapping[str, Any]) -> dict[str, Any]:
    gate_checks: dict[str, list[dict[str, Any]]] = {}
    raw_checks = quality.get("gate_checks")
    if isinstance(raw_checks, Mapping):
        for gate_name, checks in raw_checks.items():
            rows: list[dict[str, Any]] = []
            for check in checks if isinstance(checks, list) else []:
                if not isinstance(check, Mapping):
                    continue
                rows.append({
                    key: check.get(key)
                    for key in ("command", "exit_code", "passed", "status", "timed_out")
                    if check.get(key) not in (None, "")
                })
            gate_checks[str(gate_name)] = rows
    return {
        "status": str(quality.get("status") or ""),
        "failure": str(quality.get("failure") or ""),
        "gate_source": str(quality.get("gate_source") or ""),
        "gates_run": sorted(str(item) for item in quality.get("gates_run") or []),
        "gates_passed": sorted(str(item) for item in quality.get("gates_passed") or []),
        "gates_failed": sorted(str(item) for item in quality.get("gates_failed") or []),
        "gate_checks": gate_checks,
    }


__all__ = [
    "candidate_failure_envelope",
    "candidate_aggregate_event_payload",
    "candidate_integration_identity",
    "candidate_terminal_event_payload",
    "prepare_candidate_integration_attempt",
    "stable_payload_digest",
]
